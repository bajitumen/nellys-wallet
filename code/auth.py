import logging
from typing import Optional

import jwt

import config
from models import User

log = logging.getLogger(__name__)


def clerk_enabled() -> bool:
    return bool(config.CLERK_JWT_PUBLIC_KEY)


def verify_session_cookie(token: Optional[str]) -> Optional[str]:
    if not clerk_enabled() or not token:
        return None
    try:
        claims = jwt.decode(
            token,
            config.CLERK_JWT_PUBLIC_KEY,
            algorithms=["RS256"],
            # Clerk aud isn't pinned; signature + expiry is sufficient here.
            options={"verify_aud": False},
        )
    except jwt.InvalidTokenError as e:
        log.warning("Clerk session verification failed: %s", e)
        return None
    return claims.get("sub")


def find_or_create_user(
    clerk_user_id: str, email: Optional[str], session
) -> User:
    # No auto-claim of placeholder row — would let first sign-up inherit dev's creds.
    user = (
        session.query(User)
        .filter_by(clerk_user_id=clerk_user_id)
        .one_or_none()
    )
    if user is not None:
        return user

    user = User(clerk_user_id=clerk_user_id, email=email or "")
    session.add(user)
    session.commit()
    log.info("Created new user from Clerk session: %s", clerk_user_id)
    return user


def get_current_user(request, session) -> Optional[User]:
    if not clerk_enabled():
        return session.query(User).first()
    clerk_user_id = verify_session_cookie(request.cookies.get("__session"))
    if not clerk_user_id:
        return None
    return find_or_create_user(clerk_user_id, None, session)
