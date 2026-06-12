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
        require = ["exp", "sub"]
        decode_kwargs = {
            "algorithms": ["RS256"],
        }
        if config.CLERK_ISSUER:
            decode_kwargs["issuer"] = config.CLERK_ISSUER
            require.append("iss")
        if config.CLERK_AUTHORIZED_PARTIES:
            require.append("azp")
        decode_kwargs["options"] = {"verify_aud": False, "require": require}
        claims = jwt.decode(token, config.CLERK_JWT_PUBLIC_KEY, **decode_kwargs)
    except jwt.InvalidTokenError as e:
        log.warning("Clerk session verification failed: %s", e)
        return None
    if config.CLERK_AUTHORIZED_PARTIES:
        azp = claims.get("azp")
        if azp not in config.CLERK_AUTHORIZED_PARTIES:
            log.warning("Clerk session rejected: azp=%r not in allowlist", azp)
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
    if clerk_enabled():
        clerk_user_id = verify_session_cookie(request.cookies.get("__session"))
        if not clerk_user_id:
            return None
        return find_or_create_user(clerk_user_id, None, session)
    # Fail closed: anything other than an explicit dev opt-in refuses to serve.
    # Inferring "safe to skip auth" from FLASK_ENV ever sneaks back in via a
    # typo, "Prod", trailing whitespace, etc., and the cost there is leaking
    # the placeholder user's bank data to anyone who can reach the host.
    if not config.ALLOW_INSECURE_NO_AUTH:
        log.error(
            "Refusing to serve request: Clerk is disabled and "
            "ALLOW_INSECURE_NO_AUTH is not set. Set CLERK_JWT_PUBLIC_KEY (and "
            "CLERK_ISSUER / CLERK_AUTHORIZED_PARTIES) in this environment, or "
            "ALLOW_INSECURE_NO_AUTH=1 to acknowledge serving unauthenticated."
        )
        return None
    return session.query(User).first()
