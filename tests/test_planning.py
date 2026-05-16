"""Planning page: per-account rate persistence + /planning routes."""

from unittest.mock import patch


def _empty_fetch_all():
    return {
        "cash": [], "credit": [], "investment": [], "other": [], "errors": [],
    }


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

def test_planning_view_no_user(client):
    r = client.get("/planning")
    assert r.status_code == 200
    assert b"No user provisioned" in r.data


def test_planning_view_renders_accounts(client, user_with_item):
    """Accounts from fetch_all show up in the rates table."""
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
        r = client.get("/planning")
    assert r.status_code == 200
    assert b"Total net worth" in r.data
    assert b"Checking" in r.data
    assert b"Credit Card" in r.data


def test_planning_rate_save_creates(client, user_with_item, db_session):
    from models import AccountRate
    r = client.post("/planning/rate/acct_cash", json={"rate": 4.5})
    assert r.status_code == 200
    assert r.get_json()["rate"] == 4.5
    row = db_session.query(AccountRate).one()
    assert row.plaid_account_id == "acct_cash"
    assert row.rate == 4.5


def test_planning_rate_save_updates(client, user_with_item, db_session):
    from models import AccountRate
    client.post("/planning/rate/acct_cash", json={"rate": 4.5})
    client.post("/planning/rate/acct_cash", json={"rate": 5.5})
    rows = db_session.query(AccountRate).all()
    assert len(rows) == 1
    assert rows[0].rate == 5.5


def test_planning_rate_save_null_clears(client, user_with_item, db_session):
    from models import AccountRate
    client.post("/planning/rate/acct_cash", json={"rate": 4.5})
    r = client.post("/planning/rate/acct_cash", json={"rate": None})
    assert r.status_code == 200
    assert db_session.query(AccountRate).count() == 0


def test_planning_rate_save_rejects_non_numeric(client, user_with_item):
    r = client.post("/planning/rate/acct_cash", json={"rate": "not-a-number"})
    assert r.status_code == 400
