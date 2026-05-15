"""Load environment configuration from .env."""

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
FLASK_ENV = os.environ.get("FLASK_ENV", "development")

# Clerk — empty strings allowed during the pre-Clerk build phase.
CLERK_PUBLISHABLE_KEY = os.environ.get("CLERK_PUBLISHABLE_KEY", "")
CLERK_SECRET_KEY = os.environ.get("CLERK_SECRET_KEY", "")
CLERK_JWT_PUBLIC_KEY = os.environ.get("CLERK_JWT_PUBLIC_KEY", "")
# Clerk's frontend API host, e.g. "clerk.example.com" for prod or
# "<slug>.clerk.accounts.dev" for dev. Shown in the Clerk dashboard.
CLERK_FRONTEND_API = os.environ.get("CLERK_FRONTEND_API", "")

INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
os.makedirs(INSTANCE_DIR, exist_ok=True)


# Configure root logging once, here, since this module is the import-time
# bootstrap. Module-level loggers (via logging.getLogger(__name__)) inherit.
_LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=_LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
