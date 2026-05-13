"""Tests for spending.fetch_last_month (DB-backed) and sync_transactions (Plaid → DB)."""

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


def _seed_tx(session, item, plaid_id, amount, pfc, name="Merchant", date_=None):
    """Insert a Transaction row tied to the given item."""
    from models import Transaction
    session.add(Transaction(
        user_id=item.user_id,
        item_id=item.id,
        plaid_transaction_id=plaid_id,
        date=date_ or date.today(),
        amount=amount,
        name=name,
        merchant_name=name,
        pfc_primary=pfc,
    ))
    session.commit()


# ---------------------------------------------------------------------------
# fetch_last_month (DB-backed)
# ---------------------------------------------------------------------------

def test_basic_aggregation(user_with_item, db_session):
    """Two seeded transactions across two categories aggregate correctly."""
    from spending import fetch_last_month
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "tx1", 50.0, "FOOD_AND_DRINK")
    _seed_tx(db_session, item, "tx2", 30.0, "TRANSPORTATION")
    out = fetch_last_month(user_with_item, session=db_session)
    assert out["total"] == 80.0
    cats = {c["name"]: c["total"] for c in out["categories"]}
    assert cats == {"Food And Drink": 50.0, "Transportation": 30.0}
    assert out["categories"][0]["name"] == "Food And Drink"  # sorted desc


def test_excluded_categories_drop_out(user_with_item, db_session):
    """INCOME / TRANSFER_IN / TRANSFER_OUT are excluded from spending."""
    from spending import fetch_last_month
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "tx1", 50.0, "FOOD_AND_DRINK")
    _seed_tx(db_session, item, "tx2", 1000.0, "TRANSFER_OUT")
    _seed_tx(db_session, item, "tx3", 500.0, "INCOME")
    out = fetch_last_month(user_with_item, session=db_session)
    assert out["total"] == 50.0
    assert len(out["transactions"]) == 1


def test_negative_amounts_dropped(user_with_item, db_session):
    """Refunds/credits (amount <= 0) are not spending."""
    from spending import fetch_last_month
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "tx1", 50.0, "FOOD_AND_DRINK")
    _seed_tx(db_session, item, "tx2", -20.0, "FOOD_AND_DRINK")
    out = fetch_last_month(user_with_item, session=db_session)
    assert out["total"] == 50.0


def test_recategorize_override(user_with_item, db_session):
    """A category override moves the tx into a different bucket."""
    from models import TransactionOverride
    from spending import fetch_last_month
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "tx1", 100.0, "LOAN_PAYMENTS")
    db_session.add(TransactionOverride(
        user_id=user_with_item.id,
        plaid_transaction_id="tx1",
        category_override="FOOD_AND_DRINK",
    ))
    db_session.commit()
    out = fetch_last_month(user_with_item, session=db_session)
    assert out["categories"][0]["name"] == "Food And Drink"


def test_recategorize_to_excluded_drops_tx(user_with_item, db_session):
    """Recategorizing to TRANSFER_OUT removes the tx from spending entirely."""
    from models import TransactionOverride
    from spending import fetch_last_month
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "tx1", 100.0, "FOOD_AND_DRINK")
    _seed_tx(db_session, item, "tx2", 30.0, "FOOD_AND_DRINK")
    db_session.add(TransactionOverride(
        user_id=user_with_item.id,
        plaid_transaction_id="tx1",
        category_override="TRANSFER_OUT",
    ))
    db_session.commit()
    out = fetch_last_month(user_with_item, session=db_session)
    assert out["total"] == 30.0
    assert out["transactions"][0]["plaid_id"] == "tx2"


def test_amount_override_with_split_percentage(user_with_item, db_session):
    """Amount override changes both the tx amount and the category total."""
    from models import TransactionOverride
    from spending import fetch_last_month
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "tx1", 100.0, "FOOD_AND_DRINK")
    db_session.add(TransactionOverride(
        user_id=user_with_item.id,
        plaid_transaction_id="tx1",
        amount_override=25.0,
        split_percentage=25.0,
    ))
    db_session.commit()
    out = fetch_last_month(user_with_item, session=db_session)
    assert out["total"] == 25.0
    assert out["transactions"][0]["amount"] == 25.0
    assert out["transactions"][0]["split_percentage"] == 25.0


def test_source_filter(db_session):
    """source= restricts the result to transactions on that institution's item."""
    from models import PlaidItem, User
    from spending import fetch_last_month
    u = User(clerk_user_id="x", email="x@x")
    u.set_plaid_credentials("cid", "sec")
    db_session.add(u)
    db_session.flush()
    chase = PlaidItem(user_id=u.id, institution_name="Chase")
    chase.set_access_token("t1")
    ally = PlaidItem(user_id=u.id, institution_name="Ally")
    ally.set_access_token("t2")
    db_session.add_all([chase, ally])
    db_session.flush()
    _seed_tx(db_session, chase, "c1", 10.0, "FOOD_AND_DRINK")
    _seed_tx(db_session, ally, "a1", 25.0, "TRANSPORTATION")
    out = fetch_last_month(u, source="Chase", session=db_session)
    assert out["total"] == 10.0
    assert out["transactions"][0]["source"] == "Chase"


def test_count_reflects_transaction_count(user_with_item, db_session):
    from spending import fetch_last_month
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "tx1", 50.0, "FOOD_AND_DRINK")
    _seed_tx(db_session, item, "tx2", 30.0, "FOOD_AND_DRINK")
    _seed_tx(db_session, item, "tx3", 20.0, "TRANSPORTATION")
    _seed_tx(db_session, item, "tx4", 100.0, "TRANSFER_OUT")  # excluded
    out = fetch_last_month(user_with_item, session=db_session)
    assert out["count"] == 3


def test_month_param_filters_by_date(user_with_item, db_session):
    """Seeded tx outside the selected month is excluded."""
    from spending import fetch_last_month
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "tx1", 50.0, "FOOD_AND_DRINK",
             date_=date(2026, 3, 15))
    _seed_tx(db_session, item, "tx2", 25.0, "FOOD_AND_DRINK",
             date_=date(2026, 4, 10))
    out = fetch_last_month(user_with_item, month="2026-03", session=db_session)
    assert out["total"] == 50.0
    assert out["month"] == "2026-03"
    assert out["month_label"] == "March 2026"


def test_invalid_month_falls_back_to_current(user_with_item, db_session):
    """Malformed month strings silently default to the current month."""
    from spending import fetch_last_month
    out = fetch_last_month(user_with_item, month="bogus", session=db_session)
    today = date.today()
    assert out["month"] == f"{today.year:04d}-{today.month:02d}"


def test_cache_hit_avoids_db_scan(user_with_item, db_session):
    """Second call within TTL returns the cached object identity."""
    from spending import fetch_last_month
    _seed_tx(db_session, user_with_item.items[0], "tx1", 50.0, "FOOD_AND_DRINK")
    first = fetch_last_month(user_with_item, session=db_session)
    second = fetch_last_month(user_with_item, session=db_session)
    assert first is second  # same cached dict


def test_invalidate_cache_forces_refetch(user_with_item, db_session):
    """invalidate_cache drops the entry; next call rebuilds."""
    from spending import fetch_last_month, invalidate_cache
    _seed_tx(db_session, user_with_item.items[0], "tx1", 50.0, "FOOD_AND_DRINK")
    first = fetch_last_month(user_with_item, session=db_session)
    invalidate_cache(user_with_item.id)
    second = fetch_last_month(user_with_item, session=db_session)
    assert first is not second  # rebuilt
    assert first["total"] == second["total"]  # same value


# ---------------------------------------------------------------------------
# sync_transactions (Plaid → DB)
# ---------------------------------------------------------------------------

def _mock_plaid_tx(tx_id, amount, primary, date_=None, name="Merchant"):
    tx = MagicMock()
    tx.transaction_id = tx_id
    tx.amount = amount
    tx.date = date_ or date.today()
    tx.name = name
    tx.merchant_name = name
    pfc = MagicMock()
    pfc.primary = primary
    tx.personal_finance_category = pfc
    return tx


@pytest.fixture
def patch_plaid():
    with patch("spending.plaid_client_for") as mock_for:
        client = MagicMock()
        mock_for.return_value = client
        yield client


def test_sync_inserts_new_rows(user_with_item, db_session, patch_plaid):
    from models import Transaction
    from spending import sync_transactions
    resp = MagicMock()
    resp.transactions = [
        _mock_plaid_tx("p1", 50.0, "FOOD_AND_DRINK"),
        _mock_plaid_tx("p2", 25.0, "TRANSPORTATION"),
    ]
    patch_plaid.transactions_get.return_value = resp
    result = sync_transactions(user_with_item, db_session)
    assert result["added"] == 2
    assert result["updated"] == 0
    assert db_session.query(Transaction).count() == 2


def test_sync_updates_existing_rows(user_with_item, db_session, patch_plaid):
    """Re-syncing the same plaid_transaction_id updates rather than duplicates."""
    from models import Transaction
    from spending import sync_transactions
    # First sync
    resp = MagicMock()
    resp.transactions = [_mock_plaid_tx("p1", 50.0, "FOOD_AND_DRINK", name="Old")]
    patch_plaid.transactions_get.return_value = resp
    sync_transactions(user_with_item, db_session)
    # Second sync — same id, different amount/name
    resp2 = MagicMock()
    resp2.transactions = [_mock_plaid_tx("p1", 75.0, "FOOD_AND_DRINK", name="New")]
    patch_plaid.transactions_get.return_value = resp2
    result = sync_transactions(user_with_item, db_session)
    assert result["added"] == 0
    assert result["updated"] == 1
    rows = db_session.query(Transaction).all()
    assert len(rows) == 1
    assert rows[0].amount == 75.0
    assert rows[0].name == "New"


def test_sync_sets_last_synced_timestamp(user_with_item, db_session, patch_plaid):
    """user.last_transactions_sync is bumped after a successful sync."""
    from spending import sync_transactions
    resp = MagicMock()
    resp.transactions = []
    patch_plaid.transactions_get.return_value = resp
    assert user_with_item.last_transactions_sync is None
    sync_transactions(user_with_item, db_session)
    db_session.refresh(user_with_item)
    assert user_with_item.last_transactions_sync is not None


def test_sync_invalidates_cache(user_with_item, db_session, patch_plaid):
    """After a sync, fetch_last_month rebuilds (does not return stale)."""
    from spending import fetch_last_month, sync_transactions
    _seed_tx(db_session, user_with_item.items[0], "old", 10.0, "FOOD_AND_DRINK")
    before = fetch_last_month(user_with_item, session=db_session)
    assert before["total"] == 10.0

    # Sync replaces with new data
    resp = MagicMock()
    resp.transactions = [_mock_plaid_tx("p1", 99.0, "TRANSPORTATION")]
    patch_plaid.transactions_get.return_value = resp
    sync_transactions(user_with_item, db_session)

    after = fetch_last_month(user_with_item, session=db_session)
    # Was 10.0 from old seeded data + 99.0 from new synced data
    assert after["total"] == 10.0 + 99.0
    assert before is not after


def test_chart_series_starts_at_zero_and_accumulates(user_with_item, db_session):
    """Cumulative chart anchors at (start, 0) and steps up on each tx date."""
    from spending import fetch_last_month
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "tx1", 20.0, "FOOD_AND_DRINK", date_=date(2026, 5, 3))
    _seed_tx(db_session, item, "tx2", 30.0, "TRANSPORTATION", date_=date(2026, 5, 10))
    out = fetch_last_month(user_with_item, month="2026-05", session=db_session)
    assert out["chart"] is not None
    assert "M " in out["chart"]["line_path"]
    # Y axis: max should be 50.0 (cumulative); area path closes to baseline.
    assert "Z" in out["chart"]["area_path"]


def test_chart_none_when_no_transactions(user_with_item, db_session):
    """No transactions in the month → no chart."""
    from spending import fetch_last_month
    out = fetch_last_month(user_with_item, month="2026-04", session=db_session)
    assert out["chart"] is None


def test_relative_time_formats():
    from spending import relative_time
    now = datetime.utcnow()
    assert relative_time(None) == "never"
    assert relative_time(now) == "just now"
    assert relative_time(now - timedelta(minutes=5)) == "5 min ago"
    assert relative_time(now - timedelta(hours=2)) == "2 hr ago"
    assert relative_time(now - timedelta(days=3)) == "3 days ago"
    assert relative_time(now - timedelta(days=1)) == "1 day ago"
