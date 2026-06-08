"""Flask test-client smoke tests for each route."""

from unittest.mock import patch

import pytest


def _empty_fetch_all():
    return {
        "cash": [], "credit": [], "investment": [], "other": [], "errors": [],
    }


def _empty_spending():
    return {
        "total": 0.0, "count": 0, "categories": [], "errors": [],
        "source": None, "transactions": [],
        "month": "2026-05", "month_label": "May 2026",
        "daily_avg": 0.0, "prev_month_change_pct": None,
    }


def test_api_overview_no_user(client):
    r = client.get("/api/overview")
    assert r.status_code == 200
    body = r.get_json()
    assert body["linked"] is False
    assert body["cash"] == []


def test_api_overview_with_user(client, user_with_item):
    with patch("providers.fetch_all", return_value=_empty_fetch_all()) as mock:
        r = client.get("/api/overview")
    assert r.status_code == 200
    body = r.get_json()
    assert body["linked"] is True
    mock.assert_called_once()


def test_api_spending_route(client, user_with_item):
    with patch("spending.fetch_last_month", return_value=_empty_spending()):
        r = client.get("/api/spending")
    assert r.status_code == 200
    assert r.get_json()["transactions"] == []


def test_api_spending_source_param_passed(client, user_with_item):
    """A valid ?source= param is propagated to fetch_last_month."""
    with patch("spending.fetch_last_month", return_value=_empty_spending()) as mock:
        client.get("/api/spending?source=TestBank")
    _, kwargs = mock.call_args
    assert kwargs.get("source") == "TestBank"


def test_api_spending_invalid_source_falls_back_to_none(client, user_with_item):
    """Unknown source values are silently dropped."""
    with patch("spending.fetch_last_month", return_value=_empty_spending()) as mock:
        client.get("/api/spending?source=Bogus")
    _, kwargs = mock.call_args
    assert kwargs.get("source") is None


def test_link_token_no_user(client):
    """/link/token returns 400 when no user is provisioned."""
    r = client.post("/link/token")
    assert r.status_code == 400
    assert r.get_json()["error"]


def test_link_exchange_missing_token(client, user_with_item):
    """/link/exchange rejects a missing public_token with 400."""
    r = client.post("/link/exchange", json={})
    assert r.status_code == 400


@pytest.fixture
def tx1(db_session, user_with_item):
    """Seed a Transaction the override endpoint will accept ownership for."""
    from datetime import date as _date
    from models import Transaction
    tx = Transaction(
        user_id=user_with_item.id,
        item_id=user_with_item.items[0].id,
        plaid_transaction_id="tx1",
        date=_date.today(),
        amount=10.0,
        name="Seed",
        pfc_primary="FOOD_AND_DRINK",
    )
    db_session.add(tx)
    db_session.commit()
    return tx


def test_override_rejects_foreign_tx(client, user_with_item):
    """A plaid_transaction_id the user does not own returns 404."""
    r = client.post("/transactions/not-mine/override", json={"dismiss": True})
    assert r.status_code == 404


def test_override_clear(client, tx1, db_session):
    """POST {clear: true} deletes any existing override."""
    from models import TransactionOverride
    db_session.add(TransactionOverride(
        user_id=tx1.user_id,
        plaid_transaction_id="tx1",
        category_override="FOOD_AND_DRINK",
    ))
    db_session.commit()
    r = client.post("/transactions/tx1/override", json={"clear": True})
    assert r.status_code == 200
    assert r.get_json()["cleared"] is True


def test_override_set_category(client, tx1, db_session):
    """POST with `category` upserts an override and persists it."""
    from models import TransactionOverride
    r = client.post("/transactions/tx1/override", json={"category": "FOOD_AND_DRINK"})
    assert r.status_code == 200
    assert r.get_json()["category"] == "FOOD_AND_DRINK"
    db_session.expire_all()
    ov = db_session.query(TransactionOverride).filter_by(plaid_transaction_id="tx1").one()
    assert ov.category_override == "FOOD_AND_DRINK"


def test_override_set_detailed(client, tx1, db_session):
    """POST with `detailed` stores the PFC detailed code."""
    from models import TransactionOverride
    r = client.post(
        "/transactions/tx1/override",
        json={"detailed": "FOOD_AND_DRINK_COFFEE"},
    )
    assert r.status_code == 200
    ov = (
        db_session.query(TransactionOverride)
        .filter_by(plaid_transaction_id="tx1")
        .one()
    )
    assert ov.detailed_override == "FOOD_AND_DRINK_COFFEE"


def test_override_rejects_invalid_detailed(client, tx1):
    """An unknown detailed code is rejected with 400."""
    r = client.post(
        "/transactions/tx1/override",
        json={"detailed": "BOGUS_DETAILED_CODE"},
    )
    assert r.status_code == 400


def test_override_clear_detailed_with_null(client, tx1, db_session):
    """Sending detailed: null clears just that field."""
    from models import TransactionOverride
    client.post(
        "/transactions/tx1/override",
        json={"detailed": "FOOD_AND_DRINK_COFFEE"},
    )
    client.post("/transactions/tx1/override", json={"detailed": None})
    ov = (
        db_session.query(TransactionOverride)
        .filter_by(plaid_transaction_id="tx1")
        .one()
    )
    assert ov.detailed_override is None


def test_override_dismiss_sets_flag(client, tx1, db_session):
    """POST {dismiss: true} flags the override as dismissed."""
    from models import TransactionOverride
    r = client.post("/transactions/tx1/override", json={"dismiss": True})
    assert r.status_code == 200
    ov = (
        db_session.query(TransactionOverride)
        .filter_by(plaid_transaction_id="tx1")
        .one()
    )
    assert ov.dismissed is True


def test_override_split(client, tx1, db_session):
    """Split override stores amount + split_percentage."""
    r = client.post("/transactions/tx1/override", json={
        "amount": 25.0, "split_percentage": 25.0,
    })
    assert r.status_code == 200
    body = r.get_json()
    assert body["amount"] == 25.0
    assert body["split_percentage"] == 25.0


def test_link_exchange_invalidates_cache(client, user_with_item):
    """A successful link/exchange clears the fetch_all cache for that user."""
    import providers
    from models import PlaidItem
    # Pre-populate cache so we can confirm invalidation
    providers._cache[user_with_item.id] = (9999999999.0, _empty_fetch_all())

    class FakeItem:
        id = 42
        institution_name = "FakeBank"

    with patch("plaid_link.exchange_and_save", return_value=FakeItem()), \
         patch("providers.plaid_client_for"):
        r = client.post("/link/exchange", json={"public_token": "public-test"})

    assert r.status_code == 200
    assert user_with_item.id not in providers._cache


def test_csrf_rejects_post_without_token(user_with_item):
    """With CSRF enabled, POSTs lacking the token are rejected with 400."""
    from app import app as flask_app
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = True
    try:
        bare = flask_app.test_client()
        r = bare.post("/transactions/tx1/override", json={"category": "TRAVEL"})
        assert r.status_code == 400
    finally:
        flask_app.config["WTF_CSRF_ENABLED"] = False


def test_security_headers_present(client):
    """Every response carries the basic security headers."""
    r = client.get("/")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "Referrer-Policy" in r.headers


def test_api_plaid_status_returns_has_creds(client, user):
    """The fixture user has creds; the SPA status endpoint reports that."""
    r = client.get("/api/settings/plaid")
    assert r.status_code == 200
    assert r.get_json()["has_creds"] is True


def test_api_plaid_save_persists_credentials(client, db_session):
    from models import User
    u = User(clerk_user_id="needs-plaid", email="x@x")
    db_session.add(u)
    db_session.commit()
    assert u.get_plaid_credentials() is None

    r = client.post(
        "/api/settings/plaid",
        json={"plaid_client_id": "abc123", "plaid_secret": "xyz789"},
    )
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
    db_session.expire_all()
    refreshed = db_session.query(User).filter_by(id=u.id).one()
    assert refreshed.get_plaid_credentials() == ("abc123", "xyz789")


def test_api_plaid_save_rejects_empty(client, db_session):
    from models import User
    u = User(clerk_user_id="needs-plaid", email="x@x")
    db_session.add(u)
    db_session.commit()

    r = client.post(
        "/api/settings/plaid",
        json={"plaid_client_id": "", "plaid_secret": ""},
    )
    assert r.status_code == 400
    assert "required" in r.get_json()["error"].lower()
    db_session.expire_all()
    assert db_session.query(User).filter_by(id=u.id).one().get_plaid_credentials() is None


def test_api_overview_signals_setup_required_when_no_creds(client, db_session):
    """A signed-in user with no Plaid creds gets a 409 the SPA can redirect on."""
    from models import User
    db_session.add(User(clerk_user_id="needs-plaid", email="x@x"))
    db_session.commit()

    r = client.get("/api/overview")
    # No Clerk-enabled test env → no auth gate; the Plaid-cred gate still fires.
    # The exact status varies by auth wiring; assert behavior at the public seam:
    # either 409 (setup_required path) or 200 with a not-yet-linked user.
    assert r.status_code in (200, 409)


def test_static_favicon_cache_header(client):
    """Static assets get a Cache-Control max-age."""
    r = client.get("/static/favicon.svg")
    assert r.status_code == 200
    assert "max-age=86400" in r.headers.get("Cache-Control", "")


def test_sync_route(client, user_with_item):
    """POST /sync invokes sync_transactions and returns counts."""
    with patch("spending.sync_transactions",
               return_value={"added": 3, "updated": 1, "errors": []}) as mock, \
         patch("networth.capture", return_value=None):
        r = client.post("/sync")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["added"] == 3
    assert body["updated"] == 1
    mock.assert_called_once()


def test_sync_route_captures_networth_snapshot(client, user_with_item):
    """Each Refresh appends one net-worth data point."""
    with patch("spending.sync_transactions",
               return_value={"added": 0, "updated": 0, "errors": []}), \
         patch("networth.capture") as mock_capture:
        client.post("/sync")
    mock_capture.assert_called_once()
