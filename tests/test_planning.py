"""Planning page: per-account rate persistence + /planning routes."""

from datetime import datetime
from unittest.mock import patch

import pytest


def _empty_fetch_all():
    return {
        "cash": [], "credit": [], "investment": [], "other": [], "errors": [],
    }


@pytest.fixture
def owned_account(db_session, user_with_item):
    """Seed an AccountBalanceSnapshot so the ownership check passes for acct_cash."""
    from models import AccountBalanceSnapshot
    db_session.add(AccountBalanceSnapshot(
        user_id=user_with_item.id,
        item_id=user_with_item.items[0].id,
        plaid_account_id="acct_cash",
        account_name="Checking",
        institution_name="TestBank",
        bucket="cash",
        balance=100.0,
        taken_at=datetime.utcnow(),
    ))
    db_session.commit()
    return "acct_cash"


# ---------------------------------------------------------------------------
# planning module
# ---------------------------------------------------------------------------

def test_get_rates_empty(user, db_session):
    from planning import get_rates
    assert get_rates(user, db_session) == {}


def test_upsert_then_get(user, db_session):
    from planning import get_rates, upsert_rate
    upsert_rate(user, "acct1", 4.5, db_session)
    upsert_rate(user, "acct2", 7.0, db_session)
    assert get_rates(user, db_session) == {"acct1": 4.5, "acct2": 7.0}


def test_upsert_updates_existing(user, db_session):
    from planning import get_rates, upsert_rate
    upsert_rate(user, "acct1", 4.5, db_session)
    upsert_rate(user, "acct1", 5.5, db_session)
    assert get_rates(user, db_session) == {"acct1": 5.5}


def test_clear_rate_removes_row(user, db_session):
    from planning import clear_rate, get_rates, upsert_rate
    upsert_rate(user, "acct1", 4.5, db_session)
    clear_rate(user, "acct1", db_session)
    assert get_rates(user, db_session) == {}


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------

def test_api_planning_no_user(client):
    r = client.get("/api/planning")
    assert r.status_code == 200
    assert r.get_json()["accounts"] == []


def test_api_planning_returns_accounts(client, user_with_item):
    """Accounts from fetch_all are surfaced by the JSON endpoint."""
    fetch_data = {
        "cash": [{
            "institution": "TestBank", "logo": None, "primary_color": None,
            "name": "Checking", "type": "Checking", "mask": "1234",
            "balance": 1000.0, "available": 1000.0,
            "plaid_account_id": "acct_cash",
        }],
        "credit": [{
            "institution": "TestBank", "logo": None, "primary_color": None,
            "name": "Credit Card", "type": "Credit Card", "mask": "5678",
            "balance": 500.0, "available": None,
            "plaid_account_id": "acct_cc",
        }],
        "investment": [], "other": [], "errors": [],
    }
    with patch("providers.fetch_all", return_value=fetch_data):
        r = client.get("/api/planning")
    assert r.status_code == 200
    names = {a["name"] for a in r.get_json()["accounts"]}
    assert names == {"Checking", "Credit Card"}


def test_planning_rate_save_creates(client, owned_account, db_session):
    from models import AccountRate
    r = client.post("/planning/rate/acct_cash", json={"rate": 4.5})
    assert r.status_code == 200
    assert r.get_json()["rate"] == 4.5
    row = db_session.query(AccountRate).one()
    assert row.plaid_account_id == "acct_cash"
    assert row.rate == 4.5


def test_planning_rate_save_updates(client, owned_account, db_session):
    from models import AccountRate
    client.post("/planning/rate/acct_cash", json={"rate": 4.5})
    client.post("/planning/rate/acct_cash", json={"rate": 5.5})
    rows = db_session.query(AccountRate).all()
    assert len(rows) == 1
    assert rows[0].rate == 5.5


def test_planning_rate_save_null_clears(client, owned_account, db_session):
    from models import AccountRate
    client.post("/planning/rate/acct_cash", json={"rate": 4.5})
    r = client.post("/planning/rate/acct_cash", json={"rate": None})
    assert r.status_code == 200
    assert db_session.query(AccountRate).count() == 0


def test_planning_rate_save_rejects_non_numeric(client, owned_account):
    r = client.post("/planning/rate/acct_cash", json={"rate": "not-a-number"})
    assert r.status_code == 400


def test_planning_rate_save_rejects_foreign_account(client, user_with_item, db_session):
    """No snapshot for this account → 404, regardless of payload."""
    r = client.post("/planning/rate/not_my_account", json={"rate": 4.5})
    assert r.status_code == 404


def test_planning_contribution_save_rejects_foreign_account(client, user_with_item):
    r = client.post("/planning/contribution/not_my_account", json={"value": 100})
    assert r.status_code == 404
