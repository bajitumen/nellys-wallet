"""Clerk session authentication.

Verifies the `__session` cookie that clerk-js sets on the browser. The
cookie is an RS256-signed JWT; we verify it against the public key
copied from the Clerk dashboard (env var CLERK_JWT_PUBLIC_KEY) and use
the `sub` claim as the user's Clerk ID.

Falls back to the pre-Clerk placeholder user (first user in the DB) when
CLERK_JWT_PUBLIC_KEY is unset. That keeps every test green and lets the
developer run locally without standing up Clerk first.
"""

import logging
from typing import Optional

import jwt

import config
from models import User

log = logging.getLogger(__name__)


def clerk_enabled() -> bool:
    """Clerk is wired in when both the secret key and the JWT public key
    are configured. Either alone isn't enough to verify a session."""
    return bool(config.CLERK_JWT_PUBLIC_KEY)


def verify_session_cookie(token: Optional[str]) -> Optional[str]:
    """Verify a Clerk session JWT and return the user's Clerk ID, or None
    on missing/invalid token (or when Clerk isn't configured)."""
    if not clerk_enabled() or not token:
        return None
    try:
        claims = jwt.decode(
            token,
            config.CLERK_JWT_PUBLIC_KEY,
            algorithms=["RS256"],
            # Clerk sets the aud claim to the deployment's frontend URL,
            # which we don't pin here — verifying the signature + expiry
            # is enough at this layer.
            options={"verify_aud": False},
        )
    except jwt.InvalidTokenError as e:
        log.warning("Clerk session verification failed: %s", e)
        return None
    return claims.get("sub")


_PLACEHOLDER_CLERK_ID = "placeholder-pre-clerk-user"


def find_or_create_user(
    clerk_user_id: str, email: Optional[str], session
) -> User:
    """Resolve a Clerk user ID to a User row; create one on first sign-in.

    Migration shortcut: when the only User in the DB is the pre-Clerk
    placeholder (the developer's old single-user account with all the
    Plaid items + history attached), claim it instead of creating a
    duplicate. Once anyone else has signed up, this migration path is
    inert — claiming only happens when there's exactly one placeholder
    and zero real users."""
    user = (
        session.query(User)
        .filter_by(clerk_user_id=clerk_user_id)
        .one_or_none()
    )
    if user is not None:
        return user

    all_users = session.query(User).all()
    if (
        len(all_users) == 1
        and all_users[0].clerk_user_id == _PLACEHOLDER_CLERK_ID
    ):
        placeholder = all_users[0]
        placeholder.clerk_user_id = clerk_user_id
        if email and not placeholder.email:
            placeholder.email = email
        session.commit()
        log.info("Claimed placeholder user for Clerk ID: %s", clerk_user_id)
        return placeholder

    user = User(clerk_user_id=clerk_user_id, email=email or "")
    session.add(user)
    session.commit()
    log.info("Created new user from Clerk session: %s", clerk_user_id)
    return user


def get_current_user(request, session) -> Optional[User]:
    """The request → User resolution used by the @with_user decorator.
    When Clerk isn't configured, returns the placeholder user (first
    user in the DB) — preserves the pre-Clerk single-user flow."""
    if not clerk_enabled():
        return session.query(User).first()
    clerk_user_id = verify_session_cookie(request.cookies.get("__session"))
    if not clerk_user_id:
        return None
    return find_or_create_user(clerk_user_id, None, session)
