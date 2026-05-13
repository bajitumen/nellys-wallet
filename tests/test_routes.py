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
        "chart": None,
    }


def test_overview_no_user(client):
    """With no user provisioned, Overview renders the no_user empty state."""
    r = client.get("/")
    assert r.status_code == 200
    assert b"No user provisioned" in r.data


def test_overview_with_user(client, user_with_item):
    """With a linked user, Overview renders the page and calls fetch_all."""
    with patch("providers.fetch_all", return_value=_empty_fetch_all()) as mock:
        r = client.get("/")
    assert r.status_code == 200
    assert b"Overview" in r.data
    assert b"id=\"add-account-btn\"" in r.data
    mock.assert_called_once()


def test_spending_route(client, user_with_item):
    with patch("spending.fetch_last_month", return_value=_empty_spending()):
        r = client.get("/spending")
    assert r.status_code == 200
    assert b"Spending" in r.data


def test_spending_source_param_passed(client, user_with_item):
    """A valid ?source= param is propagated to fetch_last_month."""
    with patch("spending.fetch_last_month", return_value=_empty_spending()) as mock:
        client.get("/spending?source=TestBank")
    _, kwargs = mock.call_args
    assert kwargs.get("source") == "TestBank"


def test_spending_invalid_source_falls_back_to_none(client, user_with_item):
    """Unknown source values are silently dropped."""
    with patch("spending.fetch_last_month", return_value=_empty_spending()) as mock:
        client.get("/spending?source=Bogus")
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


def test_override_clear(client, user_with_item, db_session):
    """POST {clear: true} deletes any existing override."""
    from models import TransactionOverride
    db_session.add(TransactionOverride(
        user_id=user_with_item.id,
        plaid_transaction_id="tx1",
        category_override="FOOD_AND_DRINK",
    ))
    db_session.commit()
    r = client.post("/transactions/tx1/override", json={"clear": True})
    assert r.status_code == 200
    assert r.get_json()["cleared"] is True


def test_override_set_category(client, user_with_item, db_session):
    """POST with `category` upserts an override and persists it."""
    from models import TransactionOverride
    r = client.post("/transactions/tx1/override", json={"category": "FOOD_AND_DRINK"})
    assert r.status_code == 200
    assert r.get_json()["category"] == "FOOD_AND_DRINK"
    db_session.expire_all()
    ov = db_session.query(TransactionOverride).filter_by(plaid_transaction_id="tx1").one()
    assert ov.category_override == "FOOD_AND_DRINK"


def test_override_split(client, user_with_item, db_session):
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


def test_static_favicon_cache_header(client):
    """Static assets get a Cache-Control max-age."""
    r = client.get("/static/favicon.svg")
    assert r.status_code == 200
    assert "max-age=86400" in r.headers.get("Cache-Control", "")


def test_sync_route(client, user_with_item):
    """POST /sync invokes sync_transactions and returns counts."""
    with patch("spending.sync_transactions",
               return_value={"added": 3, "updated": 1, "errors": []}) as mock:
        r = client.post("/sync")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["added"] == 3
    assert body["updated"] == 1
    mock.assert_called_once()
