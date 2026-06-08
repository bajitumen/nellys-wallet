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

def test_dismissed_transaction_excluded_from_totals_but_kept_in_list(user_with_item, db_session):
    """A dismissed tx stays in the transactions list (flagged) so the UI can
    render it with strikethrough + a Restore action, but is excluded from totals."""
    from models import TransactionOverride
    from spending import fetch_last_month
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "tx1", 50.0, "FOOD_AND_DRINK")
    _seed_tx(db_session, item, "tx2", 30.0, "TRANSPORTATION")
    db_session.add(TransactionOverride(
        user_id=user_with_item.id,
        plaid_transaction_id="tx1",
        dismissed=True,
    ))
    db_session.commit()
    out = fetch_last_month(user_with_item, session=db_session)
    assert out["total"] == 30.0
    assert out["count"] == 1
    assert len(out["transactions"]) == 2
    by_id = {t["plaid_id"]: t for t in out["transactions"]}
    assert by_id["tx1"]["dismissed"] is True
    assert by_id["tx2"]["dismissed"] is False


def test_dismissed_excluded_from_monthly_totals(user_with_item, db_session):
    """The Overview bar chart respects dismissed too."""
    from datetime import date
    from models import TransactionOverride
    from spending import monthly_totals
    item = user_with_item.items[0]
    today = date.today()
    _seed_tx(db_session, item, "tx1", 100.0, "FOOD_AND_DRINK", date_=today)
    _seed_tx(db_session, item, "tx2", 50.0, "FOOD_AND_DRINK", date_=today)
    db_session.add(TransactionOverride(
        user_id=user_with_item.id, plaid_transaction_id="tx1", dismissed=True,
    ))
    db_session.commit()
    out = monthly_totals(user_with_item, db_session, n_months=1)
    assert out[0]["total"] == 50.0


def test_basic_aggregation(user_with_item, db_session):
    """Two seeded transactions across two categories aggregate correctly."""
    from spending import fetch_last_month
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "tx1", 50.0, "FOOD_AND_DRINK")
    _seed_tx(db_session, item, "tx2", 30.0, "TRANSPORTATION")
    out = fetch_last_month(user_with_item, session=db_session)
    assert out["total"] == 80.0
    # The "All" tab includes every spending primary even with zero spend, so
    # filter to the ones with actual totals for the aggregation check.
    spent_cats = {c["name"]: c["total"] for c in out["categories"] if c["total"] > 0}
    assert spent_cats == {"Food and Drink": 50.0, "Transportation": 30.0}
    assert out["categories"][0]["name"] == "Food and Drink"  # sorted desc


def test_all_tab_includes_unspent_primaries(user_with_item, db_session):
    """Without a source filter, every SPENDING-side primary appears even with $0.

    Income-side primaries (INCOME / TRANSFER_IN) must NOT show up — the spending
    table is for spending categories only.
    """
    from spending import fetch_last_month
    import pfc
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "tx1", 50.0, "FOOD_AND_DRINK")
    out = fetch_last_month(user_with_item, session=db_session)
    names = {c["name"] for c in out["categories"]}
    for primary in pfc.PFC_TAXONOMY:
        if pfc.is_spend_category(primary):
            assert pfc.humanize_primary(primary) in names
        else:
            assert pfc.humanize_primary(primary) not in names
    # Unspent categories carry $0 and 0 transactions.
    travel = next(c for c in out["categories"] if c["name"] == "Travel")
    assert travel["total"] == 0.0
    assert travel["count"] == 0


def test_source_filter_omits_unspent_categories(db_session):
    """With a source filter, only categories with spending in that source appear."""
    from models import PlaidItem, User
    from spending import fetch_last_month
    u = User(clerk_user_id="x", email="x@x")
    u.set_plaid_credentials("cid", "sec")
    db_session.add(u)
    db_session.flush()
    chase = PlaidItem(user_id=u.id, institution_name="Chase")
    chase.set_access_token("t1")
    db_session.add(chase)
    db_session.flush()
    _seed_tx(db_session, chase, "tx1", 10.0, "FOOD_AND_DRINK")
    out = fetch_last_month(u, source="Chase", session=db_session)
    names = {c["name"] for c in out["categories"]}
    assert names == {"Food and Drink"}


def test_category_carries_per_primary_budget(user_with_item, db_session):
    """The Budget column on the Spending page is the sum of detailed budgets."""
    import budget
    from spending import fetch_last_month
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "tx1", 50.0, "FOOD_AND_DRINK")
    budget.upsert(user_with_item, "FOOD_AND_DRINK_COFFEE", 60.0, db_session)
    budget.upsert(user_with_item, "FOOD_AND_DRINK_GROCERIES", 200.0, db_session)
    out = fetch_last_month(user_with_item, session=db_session)
    food = next(c for c in out["categories"] if c["name"] == "Food and Drink")
    assert food["budget"] == 260.0
    # A primary with no budget rows reports 0.
    travel = next(c for c in out["categories"] if c["name"] == "Travel")
    assert travel["budget"] == 0.0


def test_excluded_categories_drop_out(user_with_item, db_session):
    """INCOME / TRANSFER_IN are excluded; TRANSFER_OUT counts (Zelle etc.)."""
    from spending import fetch_last_month
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "tx1", 50.0, "FOOD_AND_DRINK")
    _seed_tx(db_session, item, "tx2", 1000.0, "TRANSFER_OUT")
    _seed_tx(db_session, item, "tx3", 500.0, "INCOME")
    _seed_tx(db_session, item, "tx4", 200.0, "TRANSFER_IN")
    out = fetch_last_month(user_with_item, session=db_session)
    assert out["total"] == 1050.0
    assert len(out["transactions"]) == 2


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
    assert out["categories"][0]["name"] == "Food and Drink"


def test_recategorize_to_excluded_drops_tx(user_with_item, db_session):
    """Recategorizing to an excluded category (TRANSFER_IN) removes the tx."""
    from models import TransactionOverride
    from spending import fetch_last_month
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "tx1", 100.0, "FOOD_AND_DRINK")
    _seed_tx(db_session, item, "tx2", 30.0, "FOOD_AND_DRINK")
    db_session.add(TransactionOverride(
        user_id=user_with_item.id,
        plaid_transaction_id="tx1",
        category_override="TRANSFER_IN",
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
    _seed_tx(db_session, item, "tx4", 100.0, "TRANSFER_IN")  # excluded
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


def test_repeat_call_returns_same_total(user_with_item, db_session):
    """Sequential reads of the same input produce identical totals.

    Note: a 60s TTL means the second call may serve a cached payload — what
    we care about here is that the user-facing totals stay consistent.
    """
    from spending import fetch_last_month
    _seed_tx(db_session, user_with_item.items[0], "tx1", 50.0, "FOOD_AND_DRINK")
    first = fetch_last_month(user_with_item, session=db_session)
    second = fetch_last_month(user_with_item, session=db_session)
    assert first["total"] == second["total"]


# ---------------------------------------------------------------------------
# sync_transactions (Plaid → DB)
# ---------------------------------------------------------------------------

def _mock_plaid_tx(tx_id, amount, primary, date_=None, name="Merchant", detailed=None,
                   pending=False, pending_transaction_id=None):
    tx = MagicMock()
    tx.transaction_id = tx_id
    tx.amount = amount
    tx.date = date_ or date.today()
    tx.name = name
    tx.merchant_name = name
    tx.pending = pending
    tx.pending_transaction_id = pending_transaction_id
    pfc = MagicMock()
    pfc.primary = primary
    pfc.detailed = detailed  # explicit so MagicMock doesn't auto-create a child mock
    tx.personal_finance_category = pfc
    return tx


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


def test_sync_skips_pending_transactions(user_with_item, db_session, patch_plaid):
    """Pending rows from Plaid are not inserted (avoid AMEX-style duplicates)."""
    from models import Transaction
    from spending import sync_transactions
    resp = MagicMock()
    resp.transactions = [
        _mock_plaid_tx("p1", 50.0, "FOOD_AND_DRINK", pending=True),
        _mock_plaid_tx("p2", 25.0, "FOOD_AND_DRINK", pending=False),
    ]
    patch_plaid.transactions_get.return_value = resp
    result = sync_transactions(user_with_item, db_session)
    assert result["added"] == 1
    rows = db_session.query(Transaction).all()
    assert len(rows) == 1
    assert rows[0].plaid_transaction_id == "p2"


def test_sync_replaces_pending_when_posted_arrives(user_with_item, db_session, patch_plaid):
    """Posted tx with pending_transaction_id removes the prior pending row and migrates its override."""
    from models import Transaction, TransactionOverride
    from spending import sync_transactions
    # Seed a pending row (as if it slipped in before the pending-skip fix shipped)
    _seed_tx(db_session, user_with_item.items[0], "pend1", 32.53, "FOOD_AND_DRINK")
    db_session.add(TransactionOverride(
        user_id=user_with_item.id, plaid_transaction_id="pend1",
        category_override="GENERAL_MERCHANDISE",
    ))
    db_session.commit()

    resp = MagicMock()
    resp.transactions = [
        _mock_plaid_tx("post1", 32.53, "FOOD_AND_DRINK", pending_transaction_id="pend1"),
    ]
    patch_plaid.transactions_get.return_value = resp
    sync_transactions(user_with_item, db_session)

    rows = db_session.query(Transaction).all()
    assert len(rows) == 1
    assert rows[0].plaid_transaction_id == "post1"
    ov = db_session.query(TransactionOverride).one()
    assert ov.plaid_transaction_id == "post1"
    assert ov.category_override == "GENERAL_MERCHANDISE"


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


def test_transaction_carries_raw_category_and_detailed(user_with_item, db_session):
    """Each transaction surfaces both the raw primary code (for the inline
    Category dropdown) and the detailed override (for the Item dropdown)."""
    from models import TransactionOverride
    from spending import fetch_last_month
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "tx1", 50.0, "FOOD_AND_DRINK")
    db_session.add(TransactionOverride(
        user_id=user_with_item.id,
        plaid_transaction_id="tx1",
        detailed_override="FOOD_AND_DRINK_COFFEE",
    ))
    db_session.commit()
    out = fetch_last_month(user_with_item, session=db_session)
    tx = out["transactions"][0]
    assert tx["category_raw"] == "FOOD_AND_DRINK"
    assert tx["detailed_raw"] == "FOOD_AND_DRINK_COFFEE"


def test_transaction_detailed_raw_none_when_unset(user_with_item, db_session):
    from spending import fetch_last_month
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "tx1", 50.0, "FOOD_AND_DRINK")
    out = fetch_last_month(user_with_item, session=db_session)
    assert out["transactions"][0]["detailed_raw"] is None


def test_detailed_falls_back_to_plaid_when_no_override(user_with_item, db_session):
    """With no override but Plaid's pfc_detailed set, the Item populates."""
    from models import Transaction
    from spending import fetch_last_month
    item = user_with_item.items[0]
    db_session.add(Transaction(
        user_id=item.user_id, item_id=item.id,
        plaid_transaction_id="tx1", date=date.today(),
        amount=50.0, name="Cafe", merchant_name="Cafe",
        pfc_primary="FOOD_AND_DRINK",
        pfc_detailed="FOOD_AND_DRINK_COFFEE",
    ))
    db_session.commit()
    out = fetch_last_month(user_with_item, session=db_session)
    tx = out["transactions"][0]
    assert tx["detailed_raw"] == "FOOD_AND_DRINK_COFFEE"
    assert tx["detailed_label"] == "Coffee"


def test_plaid_detailed_ignored_when_primary_mismatches(user_with_item, db_session):
    """If a category override moves the row to a different primary, the
    underlying Plaid detailed (which belongs to the old primary) is dropped."""
    from models import Transaction, TransactionOverride
    from spending import fetch_last_month
    item = user_with_item.items[0]
    db_session.add(Transaction(
        user_id=item.user_id, item_id=item.id,
        plaid_transaction_id="tx1", date=date.today(),
        amount=50.0, name="Cafe", merchant_name="Cafe",
        pfc_primary="FOOD_AND_DRINK",
        pfc_detailed="FOOD_AND_DRINK_COFFEE",
    ))
    db_session.add(TransactionOverride(
        user_id=user_with_item.id, plaid_transaction_id="tx1",
        category_override="TRAVEL",  # user moved this out of Food and Drink
    ))
    db_session.commit()
    out = fetch_last_month(user_with_item, session=db_session)
    tx = out["transactions"][0]
    assert tx["category_raw"] == "TRAVEL"
    assert tx["detailed_raw"] is None
    assert tx["detailed_label"] is None


def test_sync_captures_pfc_detailed(user_with_item, db_session, patch_plaid):
    from models import Transaction
    from spending import sync_transactions
    resp = MagicMock()
    resp.transactions = [
        _mock_plaid_tx("p1", 50.0, "FOOD_AND_DRINK", detailed="FOOD_AND_DRINK_COFFEE"),
    ]
    patch_plaid.transactions_get.return_value = resp
    sync_transactions(user_with_item, db_session)
    row = db_session.query(Transaction).one()
    assert row.pfc_detailed == "FOOD_AND_DRINK_COFFEE"


def test_category_color_is_attached(user_with_item, db_session):
    """Each category entry carries the palette color keyed by raw PFC code."""
    from pfc import CATEGORY_COLORS
    from spending import fetch_last_month
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "tx1", 50.0, "FOOD_AND_DRINK")
    out = fetch_last_month(user_with_item, session=db_session)
    assert out["categories"][0]["color"] == CATEGORY_COLORS["FOOD_AND_DRINK"]


def test_daily_avg_for_past_month(user_with_item, db_session):
    """Past months always use the full month length as the denominator."""
    from spending import fetch_last_month
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "tx1", 31.0, "FOOD_AND_DRINK", date_=date(2026, 3, 5))
    out = fetch_last_month(user_with_item, month="2026-03", session=db_session)
    # March has 31 days, $31 spent → $1/day
    assert out["daily_avg"] == pytest.approx(1.0)


def test_prev_month_change_percent(user_with_item, db_session):
    """Prev-month comparison uses the same day-span: March 1-31 vs Feb 1-28."""
    from spending import fetch_last_month
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "feb1", 100.0, "FOOD_AND_DRINK", date_=date(2026, 2, 10))
    _seed_tx(db_session, item, "mar1", 150.0, "FOOD_AND_DRINK", date_=date(2026, 3, 10))
    out = fetch_last_month(user_with_item, month="2026-03", session=db_session)
    # (150 - 100) / 100 * 100 = 50%
    assert out["prev_month_change_pct"] == pytest.approx(50.0)


def test_prev_month_change_respects_prev_month_overrides(user_with_item, db_session):
    """Bug regression: overrides on prev-month transactions must be applied
    when computing the prev-month total. Previously _spending_total reused
    the current-month override map, so prev-month dismissals were silently
    ignored — making the (-X%) badge wrong."""
    from models import TransactionOverride
    from spending import fetch_last_month
    item = user_with_item.items[0]
    # Feb: $200 raw, but $100 of it is dismissed → prev total should be $100.
    _seed_tx(db_session, item, "feb1", 100.0, "FOOD_AND_DRINK", date_=date(2026, 2, 10))
    _seed_tx(db_session, item, "feb2", 100.0, "FOOD_AND_DRINK", date_=date(2026, 2, 15))
    db_session.add(TransactionOverride(
        user_id=user_with_item.id,
        plaid_transaction_id="feb2",
        dismissed=True,
    ))
    # Mar: $200 raw, no overrides → current total $200.
    _seed_tx(db_session, item, "mar1", 200.0, "FOOD_AND_DRINK", date_=date(2026, 3, 10))
    db_session.commit()
    out = fetch_last_month(user_with_item, month="2026-03", session=db_session)
    # (200 - 100) / 100 = 100%, NOT (200 - 200) / 200 = 0%
    assert out["prev_month_change_pct"] == pytest.approx(100.0)


def test_prev_month_change_none_when_no_prior_data(user_with_item, db_session):
    """No transactions in the previous month → no comparison surfaced."""
    from spending import fetch_last_month
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "tx1", 50.0, "FOOD_AND_DRINK", date_=date(2026, 4, 5))
    out = fetch_last_month(user_with_item, month="2026-04", session=db_session)
    assert out["prev_month_change_pct"] is None


def test_prev_month_respects_source_filter(db_session):
    """Source filter must apply to the previous-month total too."""
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
    # Feb: Chase $100, Ally $1000 (ignored when filtering to Chase)
    _seed_tx(db_session, chase, "c0", 100.0, "FOOD_AND_DRINK", date_=date(2026, 2, 1))
    _seed_tx(db_session, ally, "a0", 1000.0, "FOOD_AND_DRINK", date_=date(2026, 2, 1))
    # Mar: Chase $200
    _seed_tx(db_session, chase, "c1", 200.0, "FOOD_AND_DRINK", date_=date(2026, 3, 1))
    out = fetch_last_month(u, month="2026-03", source="Chase", session=db_session)
    # Chase: (200 - 100) / 100 = 100%, not (200 - 1100) / 1100
    assert out["prev_month_change_pct"] == pytest.approx(100.0)


def test_monthly_totals_returns_n_months_oldest_first(user_with_item, db_session):
    from spending import monthly_totals
    item = user_with_item.items[0]
    today = date.today()
    _seed_tx(db_session, item, "tx1", 50.0, "FOOD_AND_DRINK", date_=today)
    out = monthly_totals(user_with_item, db_session, n_months=3)
    assert len(out) == 3
    # Oldest first — last entry is the current month.
    assert out[-1]["month"] == f"{today.year:04d}-{today.month:02d}"
    assert out[-1]["total"] == 50.0
    # Earlier months are zero.
    assert out[0]["total"] == 0.0
    # Each entry carries a unix timestamp for client-side range filtering.
    for entry in out:
        assert "ts" in entry and isinstance(entry["ts"], int)


def test_available_months_scopes_by_source(user_with_item, db_session):
    """available_months returns months filtered to the chosen source institution."""
    from datetime import date
    from models import PlaidItem
    from spending import available_months
    # Existing item: TestBank — add a spending tx in April
    a = user_with_item.items[0]
    _seed_tx(db_session, a, "tx_a", 50.0, "FOOD_AND_DRINK", date_=date(2026, 4, 5))
    # Second item — add a spending tx in March
    b = PlaidItem(user_id=user_with_item.id, institution_name="OtherBank")
    b.set_access_token("dummy")
    db_session.add(b)
    db_session.commit()
    _seed_tx(db_session, b, "tx_b", 75.0, "FOOD_AND_DRINK", date_=date(2026, 3, 5))

    all_months = {m["value"] for m in available_months(user_with_item, db_session)}
    assert {"2026-04", "2026-03"} <= all_months

    only_a = {m["value"] for m in available_months(user_with_item, db_session, source="TestBank")}
    assert only_a == {"2026-04"}

    only_b = {m["value"] for m in available_months(user_with_item, db_session, source="OtherBank")}
    assert only_b == {"2026-03"}


def test_monthly_totals_excludes_categories(user_with_item, db_session):
    from spending import monthly_totals
    item = user_with_item.items[0]
    today = date.today()
    _seed_tx(db_session, item, "tx1", 100.0, "FOOD_AND_DRINK", date_=today)
    _seed_tx(db_session, item, "tx2", 500.0, "TRANSFER_IN", date_=today)
    out = monthly_totals(user_with_item, db_session, n_months=1)
    assert out[0]["total"] == 100.0


def test_monthly_totals_applies_overrides(user_with_item, db_session):
    from models import TransactionOverride
    from spending import monthly_totals
    item = user_with_item.items[0]
    today = date.today()
    _seed_tx(db_session, item, "tx1", 100.0, "FOOD_AND_DRINK", date_=today)
    db_session.add(TransactionOverride(
        user_id=user_with_item.id, plaid_transaction_id="tx1",
        amount_override=25.0,
    ))
    db_session.commit()
    out = monthly_totals(user_with_item, db_session, n_months=1)
    assert out[0]["total"] == 25.0


def test_monthly_totals_empty_for_user_without_items(user, db_session):
    from spending import monthly_totals
    assert monthly_totals(user, db_session) == []


def test_relative_time_formats():
    from datetime import timezone as _tz
    from spending import relative_time
    now = datetime.now(_tz.utc).replace(tzinfo=None)
    assert relative_time(None) == "never"
    assert relative_time(now) == "just now"
    assert relative_time(now - timedelta(minutes=5)) == "5 min ago"
    assert relative_time(now - timedelta(hours=2)) == "2 hr ago"
    assert relative_time(now - timedelta(days=3)) == "3 days ago"
    assert relative_time(now - timedelta(days=1)) == "1 day ago"
