"""Income aggregation + /income route."""

from datetime import date
from unittest.mock import patch


def _seed_inflow(session, item, plaid_id, amount, name, pfc_primary="INCOME", date_=None):
    """Insert a Transaction with Plaid's inflow sign convention (amount < 0)."""
    from models import Transaction
    session.add(Transaction(
        user_id=item.user_id,
        item_id=item.id,
        plaid_transaction_id=plaid_id,
        date=date_ or date.today(),
        amount=-abs(amount),  # money in → negative per Plaid
        name=name,
        merchant_name=name,
        pfc_primary=pfc_primary,
    ))
    session.commit()


# ---------------------------------------------------------------------------
# fetch_last_month
# ---------------------------------------------------------------------------

def test_monthly_income_totals_returns_n_months(user_with_item, db_session):
    from income import monthly_income_totals
    item = user_with_item.items[0]
    today = date.today()
    _seed_inflow(db_session, item, "in1", 2500.0, "Acme", date_=today)
    out = monthly_income_totals(user_with_item, db_session, n_months=3)
    assert len(out) == 3
    # Oldest first; current month is last.
    assert out[-1]["month"] == f"{today.year:04d}-{today.month:02d}"
    assert out[-1]["total"] == 2500.0
    assert out[0]["total"] == 0.0
    for entry in out:
        assert "ts" in entry and isinstance(entry["ts"], int)


def test_monthly_income_totals_skips_transfers(user_with_item, db_session):
    """TRANSFER_IN rows aren't classified as income."""
    from income import monthly_income_totals
    item = user_with_item.items[0]
    today = date.today()
    _seed_inflow(db_session, item, "in1", 100.0, "Acme", pfc_primary="INCOME", date_=today)
    _seed_inflow(db_session, item, "in2", 500.0, "Roommate", pfc_primary="TRANSFER_IN", date_=today)
    out = monthly_income_totals(user_with_item, db_session, n_months=1)
    assert out[0]["total"] == 100.0


def test_basic_income_aggregation(user_with_item, db_session):
    """Two inflows from two payers aggregate correctly, amounts flipped positive."""
    from income import fetch_last_month
    item = user_with_item.items[0]
    _seed_inflow(db_session, item, "in1", 2500.0, "Acme Corp")
    _seed_inflow(db_session, item, "in2", 50.0, "Schwab Dividend")
    out = fetch_last_month(user_with_item, session=db_session)
    assert out["total"] == 2550.0
    by_payer = {p["name"]: p["total"] for p in out["payers"]}
    assert by_payer == {"Acme Corp": 2500.0, "Schwab Dividend": 50.0}
    assert out["payers"][0]["name"] == "Acme Corp"  # sorted desc


def test_excludes_non_income_pfc(user_with_item, db_session):
    """TRANSFER_IN and other non-INCOME primaries are not included."""
    from income import fetch_last_month
    item = user_with_item.items[0]
    _seed_inflow(db_session, item, "in1", 100.0, "Acme", pfc_primary="INCOME")
    _seed_inflow(db_session, item, "in2", 200.0, "Friend", pfc_primary="TRANSFER_IN")
    out = fetch_last_month(user_with_item, session=db_session)
    assert out["total"] == 100.0


def test_excludes_positive_amounts(user_with_item, db_session):
    """Positive amounts on an INCOME row are spending refunds, not real income."""
    from models import Transaction
    from income import fetch_last_month
    item = user_with_item.items[0]
    # Real inflow
    _seed_inflow(db_session, item, "in1", 100.0, "Acme")
    # Pathological: INCOME with positive amount (shouldn't happen in practice)
    db_session.add(Transaction(
        user_id=item.user_id, item_id=item.id,
        plaid_transaction_id="bad", date=date.today(),
        amount=50.0, name="Weird", merchant_name="Weird",
        pfc_primary="INCOME",
    ))
    db_session.commit()
    out = fetch_last_month(user_with_item, session=db_session)
    assert out["total"] == 100.0


def test_source_filter_limits_to_one_institution(db_session):
    from models import PlaidItem, User
    from income import fetch_last_month
    u = User(clerk_user_id="x", email="x@x")
    u.set_plaid_credentials("cid", "sec")
    db_session.add(u)
    db_session.flush()
    chase = PlaidItem(user_id=u.id, institution_name="Chase")
    chase.set_access_token("t1")
    sofi = PlaidItem(user_id=u.id, institution_name="SoFi")
    sofi.set_access_token("t2")
    db_session.add_all([chase, sofi])
    db_session.flush()
    _seed_inflow(db_session, chase, "c1", 500.0, "Acme")
    _seed_inflow(db_session, sofi, "s1", 1000.0, "Acme")
    out = fetch_last_month(u, source="Chase", session=db_session)
    assert out["total"] == 500.0


def test_color_for_payer_is_stable():
    """Same payer name → same color across calls."""
    from income import PAYER_PALETTE, color_for_payer
    a = color_for_payer("Acme Corp")
    b = color_for_payer("Acme Corp")
    assert a == b
    assert a in PAYER_PALETTE


def test_color_for_payer_varies_across_payers():
    """Different payer names produce different colors at least some of the time."""
    from income import color_for_payer
    names = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M"]
    colors = {color_for_payer(n) for n in names}
    # Not all 13 names should hash to the same color slot.
    assert len(colors) > 1


def test_daily_avg_for_past_month(user_with_item, db_session):
    from income import fetch_last_month
    item = user_with_item.items[0]
    _seed_inflow(db_session, item, "in1", 31.0, "Acme", date_=date(2026, 3, 15))
    out = fetch_last_month(user_with_item, month="2026-03", session=db_session)
    # March has 31 days, $31 earned → $1/day
    assert out["daily_avg"] == 1.0


def test_prev_month_change_percent(user_with_item, db_session):
    from income import fetch_last_month
    item = user_with_item.items[0]
    _seed_inflow(db_session, item, "feb", 1000.0, "Acme", date_=date(2026, 2, 1))
    _seed_inflow(db_session, item, "mar", 1500.0, "Acme", date_=date(2026, 3, 1))
    out = fetch_last_month(user_with_item, month="2026-03", session=db_session)
    assert out["prev_month_change_pct"] == 50.0


def test_dismissed_income_excluded_from_total(user_with_item, db_session):
    """A dismissed income transaction must be excluded from the income page,
    same as Spending. Was a silent inconsistency before income started
    applying overrides."""
    from models import TransactionOverride
    from income import fetch_last_month
    item = user_with_item.items[0]
    today = date.today()
    _seed_inflow(db_session, item, "in1", 2500.0, "Acme", date_=today)
    _seed_inflow(db_session, item, "in2", 500.0, "RandomGift", date_=today)
    db_session.add(TransactionOverride(
        user_id=user_with_item.id,
        plaid_transaction_id="in2",
        dismissed=True,
    ))
    db_session.commit()
    out = fetch_last_month(user_with_item, session=db_session)
    assert out["total"] == 2500.0
    assert {t["plaid_id"] for t in out["transactions"]} == {"in1"}


def test_prev_month_change_none_when_no_prior(user_with_item, db_session):
    from income import fetch_last_month
    item = user_with_item.items[0]
    _seed_inflow(db_session, item, "in1", 100.0, "Acme", date_=date(2026, 4, 5))
    out = fetch_last_month(user_with_item, month="2026-04", session=db_session)
    assert out["prev_month_change_pct"] is None


# ---------------------------------------------------------------------------
# /income route
# ---------------------------------------------------------------------------

def _empty_income():
    return {
        "total": 0.0, "count": 0, "payers": [], "transactions": [],
        "source": None,
        "month": "2026-05", "month_label": "May 2026",
        "daily_avg": 0.0, "prev_month_change_pct": None,
    }


def test_income_route_no_user(client):
    r = client.get("/income")
    assert r.status_code == 200
    assert b"No user provisioned" in r.data


def test_income_route_renders(client, user_with_item):
    with patch("income.fetch_last_month", return_value=_empty_income()):
        r = client.get("/income")
    assert r.status_code == 200
    assert b"Income" in r.data


def test_income_route_source_filter_propagates(client, user_with_item):
    with patch("income.fetch_last_month", return_value=_empty_income()) as mock:
        client.get("/income?source=TestBank")
    _, kwargs = mock.call_args
    assert kwargs.get("source") == "TestBank"


def test_income_route_invalid_source_falls_back_to_none(client, user_with_item):
    with patch("income.fetch_last_month", return_value=_empty_income()) as mock:
        client.get("/income?source=Bogus")
    _, kwargs = mock.call_args
    assert kwargs.get("source") is None


def test_sync_invalidates_income_cache(user_with_item, db_session, patch_plaid):
    """A sync that lands a new income row clears the income cache too."""
    from income import fetch_last_month
    from models import Transaction
    from spending import sync_transactions
    item = user_with_item.items[0]
    _seed_inflow(db_session, item, "old", 100.0, "Acme")
    first = fetch_last_month(user_with_item, session=db_session)
    assert first["total"] == 100.0

    # Sync brings in a new inflow.
    from unittest.mock import MagicMock
    tx = MagicMock()
    tx.transaction_id = "p1"
    tx.amount = -200.0  # inflow
    tx.date = date.today()
    tx.name = "Acme"
    tx.merchant_name = "Acme"
    pfc = MagicMock()
    pfc.primary = "INCOME"
    pfc.detailed = None
    tx.personal_finance_category = pfc
    resp = MagicMock()
    resp.transactions = [tx]
    patch_plaid.transactions_get.return_value = resp
    sync_transactions(user_with_item, db_session)

    after = fetch_last_month(user_with_item, session=db_session)
    # Cache was busted; rebuilt to include the new $200.
    assert after["total"] == 300.0
    assert first is not after
