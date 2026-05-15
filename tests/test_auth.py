"""Clerk session verification + user resolution."""

import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


@pytest.fixture
def rsa_keypair():
    """A fresh RSA keypair per test. Returns (private_pem, public_pem)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


def _sign_session(private_pem, sub="user_abc123", expires_in=60):
    """Build a session JWT shaped like Clerk's."""
    now = int(time.time())
    return jwt.encode(
        {"sub": sub, "iat": now, "exp": now + expires_in},
        private_pem, algorithm="RS256",
    )


def test_clerk_disabled_by_default():
    """With no CLERK_JWT_PUBLIC_KEY in env, Clerk auth is off."""
    import auth
    assert auth.clerk_enabled() is False


def test_verify_session_returns_none_when_clerk_disabled():
    """Even a perfectly-signed token is rejected when Clerk isn't configured."""
    import auth
    assert auth.verify_session_cookie("anything") is None


def test_verify_session_returns_none_for_missing_token(monkeypatch, rsa_keypair):
    import auth
    monkeypatch.setattr(auth.config, "CLERK_JWT_PUBLIC_KEY", rsa_keypair[1])
    assert auth.verify_session_cookie(None) is None
    assert auth.verify_session_cookie("") is None


def test_verify_session_returns_sub_for_valid_token(monkeypatch, rsa_keypair):
    import auth
    private_pem, public_pem = rsa_keypair
    monkeypatch.setattr(auth.config, "CLERK_JWT_PUBLIC_KEY", public_pem)
    token = _sign_session(private_pem, sub="user_alice")
    assert auth.verify_session_cookie(token) == "user_alice"


def test_verify_session_rejects_expired_token(monkeypatch, rsa_keypair):
    import auth
    private_pem, public_pem = rsa_keypair
    monkeypatch.setattr(auth.config, "CLERK_JWT_PUBLIC_KEY", public_pem)
    token = _sign_session(private_pem, expires_in=-10)  # already expired
    assert auth.verify_session_cookie(token) is None


def test_verify_session_rejects_wrong_signature(monkeypatch, rsa_keypair):
    """Signed by a different key than what's configured."""
    import auth
    # Generate a different keypair to sign with
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_pem = other.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    monkeypatch.setattr(auth.config, "CLERK_JWT_PUBLIC_KEY", rsa_keypair[1])
    token = _sign_session(other_pem)
    assert auth.verify_session_cookie(token) is None


def test_find_or_create_user_creates_on_first_call(db_session):
    """A previously-unseen Clerk ID creates a new User row."""
    import auth
    from models import User
    user = auth.find_or_create_user("user_new", "new@example.com", db_session)
    assert user.clerk_user_id == "user_new"
    assert user.email == "new@example.com"
    assert db_session.query(User).count() == 1


def test_find_or_create_user_returns_existing_on_second_call(db_session):
    """Subsequent calls find the existing User instead of duplicating."""
    import auth
    from models import User
    first = auth.find_or_create_user("user_repeat", "x@y", db_session)
    second = auth.find_or_create_user("user_repeat", "x@y", db_session)
    assert first.id == second.id
    assert db_session.query(User).count() == 1


def test_get_current_user_falls_back_to_placeholder_when_clerk_disabled(
    db_session, user
):
    """Pre-Clerk: returns the first user in the DB regardless of cookies."""
    import auth
    from unittest.mock import MagicMock
    req = MagicMock()
    req.cookies = {}
    assert auth.get_current_user(req, db_session).id == user.id


def test_find_or_create_claims_lone_placeholder(db_session):
    """First sign-in claims the developer's pre-Clerk placeholder user
    instead of creating a duplicate — preserves all linked Plaid items
    and history."""
    import auth
    from models import User
    placeholder = User(
        clerk_user_id="placeholder-pre-clerk-user", email="you@local",
    )
    placeholder.set_plaid_credentials("cid", "secret")
    db_session.add(placeholder)
    db_session.commit()
    placeholder_id = placeholder.id

    user = auth.find_or_create_user("user_real", "real@example.com", db_session)
    # Same row, new clerk_user_id.
    assert user.id == placeholder_id
    assert user.clerk_user_id == "user_real"
    # Existing email is preserved (non-empty), not overwritten.
    assert user.email == "you@local"
    assert db_session.query(User).count() == 1


def test_find_or_create_fills_empty_email_on_claim(db_session):
    """If the placeholder's email is blank, Clerk-supplied email fills it in."""
    import auth
    from models import User
    placeholder = User(clerk_user_id="placeholder-pre-clerk-user", email="")
    db_session.add(placeholder)
    db_session.commit()

    user = auth.find_or_create_user("user_real", "real@example.com", db_session)
    assert user.clerk_user_id == "user_real"
    assert user.email == "real@example.com"


def test_find_or_create_does_not_claim_when_other_users_exist(db_session):
    """Once anyone else has signed up, the placeholder claim path is inert."""
    import auth
    from models import User
    db_session.add(User(clerk_user_id="placeholder-pre-clerk-user", email="dev@local"))
    db_session.add(User(clerk_user_id="user_existing", email="friend@x"))
    db_session.commit()

    new_user = auth.find_or_create_user("user_brand_new", "newbie@x", db_session)
    # No claim: brand new row was inserted.
    assert new_user.clerk_user_id == "user_brand_new"
    assert db_session.query(User).count() == 3
