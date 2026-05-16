"""Shared pytest fixtures.

Sets env vars BEFORE importing any app module so `config._required(...)` is
satisfied without touching the real .env, and points DATABASE_URL at a temp
SQLite file so the production DB is never touched.
"""

import atexit
import os
import sys
import tempfile

# Test env vars — must be set before app modules import config.
os.environ["FERNET_KEY"] = "Wx_G6C2NkMruRUn9P2Tb_KGFSKyZEQIX2KbEv_g6dkw="
os.environ["FLASK_SECRET_KEY"] = "test-secret-key"
# Force-disable Clerk in tests so @with_user uses the placeholder-user path
# instead of redirecting every request to /sign-in. Without this, a real
# CLERK_JWT_PUBLIC_KEY in the developer's .env leaks into the test process
# and breaks every route test.
os.environ["CLERK_PUBLISHABLE_KEY"] = ""
os.environ["CLERK_SECRET_KEY"] = ""
os.environ["CLERK_JWT_PUBLIC_KEY"] = ""
os.environ["CLERK_FRONTEND_API"] = ""
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"
atexit.register(lambda: os.path.exists(_tmp.name) and os.unlink(_tmp.name))

# Make `code/` importable.
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "code"))
)

import pytest
from db import Base, SessionLocal, engine


@pytest.fixture(autouse=True)
def fresh_db():
    """Drop and recreate all tables before each test."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture(autouse=True)
def clear_caches():
    """Reset all in-process caches between tests."""
    import income
    import providers
    import spending
    providers.clear_cache()
    spending.clear_cache()
    income.clear_cache()
    yield
    providers.clear_cache()
    spending.clear_cache()
    income.clear_cache()


@pytest.fixture
def db_session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def user(db_session):
    from models import User
    u = User(clerk_user_id="test-user", email="test@local")
    u.set_plaid_credentials("test_client_id", "test_secret")
    db_session.add(u)
    db_session.commit()
    return u


@pytest.fixture
def user_with_item(db_session, user):
    from models import PlaidItem
    item = PlaidItem(user_id=user.id, institution_name="TestBank")
    item.set_access_token("access-test-token")
    db_session.add(item)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def patch_plaid():
    """Mock providers.plaid_client_for via the spending module so sync_transactions
    doesn't hit real Plaid. Shared across test modules."""
    from unittest.mock import MagicMock, patch
    with patch("spending.plaid_client_for") as mock_for:
        client = MagicMock()
        mock_for.return_value = client
        yield client


@pytest.fixture
def client():
    from app import app as flask_app
    flask_app.config["TESTING"] = True
    # flask-wtf rejects POSTs without a CSRF token; tests exercise the routes
    # directly and don't render the meta tag we'd read the token from.
    flask_app.config["WTF_CSRF_ENABLED"] = False
    return flask_app.test_client()
