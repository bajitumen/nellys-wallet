import logging
import os

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))


def _required(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}. See .env.example.")
    return value


FERNET_KEY = _required("FERNET_KEY")
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///instance/finance.db")
FLASK_SECRET_KEY = _required("FLASK_SECRET_KEY")
FLASK_ENV = (os.environ.get("FLASK_ENV") or "development").strip().lower()
IS_PRODUCTION = FLASK_ENV == "production"
IS_DEVELOPMENT = FLASK_ENV == "development"
# Explicit dev-only opt-in for serving traffic without Clerk. Any other value
# (typo, unset, accidental "prod") leaves us fail-closed in get_current_user.
ALLOW_INSECURE_NO_AUTH = (
    os.environ.get("ALLOW_INSECURE_NO_AUTH", "").strip().lower() in ("1", "true", "yes")
)


def _required_in_prod(name: str) -> str:
    value = os.environ.get(name, "")
    if IS_PRODUCTION and not value:
        raise RuntimeError(
            f"Missing required env var: {name}. "
            "Required when FLASK_ENV=production to keep auth enforced."
        )
    return value


# Only the backend-verification keys are required in prod. The others are
# kept as optional reads — the publishable key is consumed by Vite at build
# time (VITE_CLERK_PUBLISHABLE_KEY), the secret key isn't used here at all,
# and CLERK_FRONTEND_API is only used to extend the CSP allowlist.
CLERK_PUBLISHABLE_KEY = os.environ.get("CLERK_PUBLISHABLE_KEY", "")
CLERK_SECRET_KEY = os.environ.get("CLERK_SECRET_KEY", "")
CLERK_JWT_PUBLIC_KEY = _required_in_prod("CLERK_JWT_PUBLIC_KEY")
CLERK_FRONTEND_API = os.environ.get("CLERK_FRONTEND_API", "")
CLERK_ISSUER = _required_in_prod("CLERK_ISSUER")
# Comma-separated list of origins the Clerk-issued session is allowed to be
# scoped to (azp claim). Required in prod so a sibling app on the same Clerk
# instance can't have its tokens accepted here.
CLERK_AUTHORIZED_PARTIES = tuple(
    p.strip() for p in _required_in_prod("CLERK_AUTHORIZED_PARTIES").split(",") if p.strip()
)
# JWKS URL for automatic signing-key rotation. Optional — if set, takes
# precedence over the static CLERK_JWT_PUBLIC_KEY. Clerk publishes this at
# <issuer>/.well-known/jwks.json. Strongly recommended in prod: a Clerk key
# rotation otherwise locks every user out until a manual redeploy.
CLERK_JWKS_URL = os.environ.get("CLERK_JWKS_URL", "").strip()

INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
os.makedirs(INSTANCE_DIR, exist_ok=True)


_LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=_LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
