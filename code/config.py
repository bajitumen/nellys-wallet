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
# Comma-separated prior keys; MultiFernet falls through to these on decrypt.
FERNET_KEY_OLD = tuple(
    k.strip() for k in os.environ.get("FERNET_KEY_OLD", "").split(",") if k.strip()
)
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///instance/finance.db")
FLASK_SECRET_KEY = _required("FLASK_SECRET_KEY")
FLASK_ENV = (os.environ.get("FLASK_ENV") or "development").strip().lower()
IS_PRODUCTION = FLASK_ENV == "production"
IS_DEVELOPMENT = FLASK_ENV == "development"
# Dev-only no-auth opt-in; anything else fail-closes in get_current_user.
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


# Only the backend-verification keys are required in prod; the rest are
# consumed at build time (Vite) or only used to extend CSP.
CLERK_PUBLISHABLE_KEY = os.environ.get("CLERK_PUBLISHABLE_KEY", "")
CLERK_SECRET_KEY = os.environ.get("CLERK_SECRET_KEY", "")
CLERK_JWT_PUBLIC_KEY = _required_in_prod("CLERK_JWT_PUBLIC_KEY")
CLERK_FRONTEND_API = _required_in_prod("CLERK_FRONTEND_API")
if IS_PRODUCTION and ("://" in CLERK_FRONTEND_API or "/" in CLERK_FRONTEND_API):
    raise RuntimeError(
        f"CLERK_FRONTEND_API must be a bare host (no scheme/path): {CLERK_FRONTEND_API!r}"
    )
CLERK_ISSUER = _required_in_prod("CLERK_ISSUER")
# Origins the azp claim must match; locks out sibling apps on the same Clerk.
CLERK_AUTHORIZED_PARTIES = tuple(
    p.strip() for p in _required_in_prod("CLERK_AUTHORIZED_PARTIES").split(",") if p.strip()
)
if IS_PRODUCTION and not CLERK_AUTHORIZED_PARTIES:
    raise RuntimeError(
        "CLERK_AUTHORIZED_PARTIES parsed empty — a stray comma silently disables azp."
    )
# Optional JWKS URL; takes precedence over the static key and survives Clerk key rotations.
CLERK_JWKS_URL = os.environ.get("CLERK_JWKS_URL", "").strip()

# Public origin (https://nellyswallet.com) — used to build the Plaid webhook URL.
# Optional: without it, link tokens are minted without a webhook and ITEM_LOGIN_REQUIRED
# events have no path home. App still serves.
APP_PUBLIC_URL = os.environ.get("APP_PUBLIC_URL", "").strip()

SENTRY_DSN = os.environ.get("SENTRY_DSN", "").strip()

# Plaid creds used for webhook signature key fetch (one shared admin client,
# not the per-user dashboard creds). Required in prod to receive webhooks.
PLAID_ADMIN_CLIENT_ID = os.environ.get("PLAID_ADMIN_CLIENT_ID", "").strip()
PLAID_ADMIN_SECRET = os.environ.get("PLAID_ADMIN_SECRET", "").strip()
PLAID_ENV = os.environ.get("PLAID_ENV", "production").strip()

INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
os.makedirs(INSTANCE_DIR, exist_ok=True)


_LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=_LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
