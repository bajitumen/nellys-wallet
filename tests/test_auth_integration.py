"""End-to-end auth tests with Clerk enabled.

The default conftest disables Clerk and sets ALLOW_INSECURE_NO_AUTH=1 so the
route tests can run without minting tokens. None of that exercises the 401
branch in with_user, which is the only thing standing between the world and
a user's bank data. These tests flip Clerk on, hit real routes, and assert
the branch behaves.
"""

import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


@pytest.fixture
def clerk_keypair():
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


@pytest.fixture
def clerk_on(monkeypatch, clerk_keypair):
    """Flip Clerk to enabled for the duration of one test."""
    import auth
    _, public_pem = clerk_keypair
    monkeypatch.setattr(auth.config, "CLERK_JWT_PUBLIC_KEY", public_pem)
    monkeypatch.setattr(auth.config, "CLERK_ISSUER", "")
    monkeypatch.setattr(auth.config, "CLERK_AUTHORIZED_PARTIES", ())
    monkeypatch.setattr(auth.config, "ALLOW_INSECURE_NO_AUTH", False)
    return clerk_keypair


def _sign(private_pem, sub="user_real", expires_in=60):
    now = int(time.time())
    return jwt.encode(
        {"sub": sub, "iat": now, "exp": now + expires_in},
        private_pem, algorithm="RS256",
    )


def test_route_returns_401_when_clerk_enabled_and_no_cookie(client, clerk_on):
    """Mutating endpoint must reject an unauthenticated request."""
    r = client.post("/transactions/anything/override", json={"dismiss": True})
    assert r.status_code == 401
    assert r.get_json()["error"] == "Not signed in"


def test_route_returns_401_when_session_cookie_is_garbage(client, clerk_on):
    """A non-JWT cookie value must not be accepted."""
    client.set_cookie(domain="localhost", key="__session", value="not-a-jwt")
    r = client.get("/api/overview")
    assert r.status_code == 401


def test_route_returns_401_when_session_cookie_is_expired(client, clerk_on):
    """An expired-but-validly-signed token must be rejected."""
    private_pem, _ = clerk_on
    expired = _sign(private_pem, expires_in=-10)
    client.set_cookie(domain="localhost", key="__session", value=expired)
    r = client.get("/api/overview")
    assert r.status_code == 401


def test_route_returns_401_when_signature_is_wrong(client, clerk_on):
    """A token signed by a different key must be rejected."""
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_pem = other.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    forged = _sign(other_pem)
    client.set_cookie(domain="localhost", key="__session", value=forged)
    r = client.get("/api/overview")
    assert r.status_code == 401


def test_route_accepts_valid_clerk_session(client, clerk_on, db_session):
    """The positive control: a real, valid session reaches the route handler."""
    from models import User
    db_session.add(User(clerk_user_id="user_real", email="r@x"))
    db_session.commit()
    # The fixture user above has no Plaid creds, which means @with_user returns
    # 409 (Plaid setup required). That's still proof that auth passed.

    private_pem, _ = clerk_on
    token = _sign(private_pem, sub="user_real")
    client.set_cookie(domain="localhost", key="__session", value=token)
    r = client.get("/api/overview")
    assert r.status_code == 409
    assert r.get_json().get("setup_required") is True
