"""Regression tests for income aggregation with overrides.

Audit gap: no test exercised a split/amount_override against an income tx,
so the sign error in _apply_income_override (override applied unflipped from
the Plaid -2500 → -1250) was silent.
"""

from datetime import date

from models import Transaction, TransactionOverride


def _seed_income(db_session, item, plaid_id, gross, name="Acme Payroll"):
    tx = Transaction(
        user_id=item.user_id, item_id=item.id,
        plaid_transaction_id=plaid_id,
        date=date.today(),
        amount=-abs(gross),  # Plaid: negative = inflow
        name=name, merchant_name=name,
        pfc_primary="INCOME",
    )
    db_session.add(tx)
    db_session.commit()
    return tx


def test_income_split_override_is_positive_in_total(user_with_item, db_session):
    """A 50% split rule on a -2500 paycheck reads as +1250, not -1250."""
    from income import fetch_last_month
    tx = _seed_income(db_session, user_with_item.items[0], "in1", 2500.0)

    # Mimic what rules.upsert_rule would write: amount_override = tx.amount * pct/100
    db_session.add(TransactionOverride(
        user_id=tx.user_id,
        plaid_transaction_id=tx.plaid_transaction_id,
        amount_override=round(tx.amount * 50 / 100.0, 2),  # = -1250.0
        split_percentage=50.0,
        source="rule",
    ))
    db_session.commit()

    out = fetch_last_month(user_with_item, session=db_session)
    assert out["total"] == 1250.0, (
        "Sign error: override amount should land as a positive inflow "
        f"in the income total, got {out['total']}"
    )


def test_monthly_cashflow_income_with_amount_override(user_with_item, db_session):
    """The cashflow income branch must apply the same sign treatment as fetch_last_month."""
    from spending import monthly_cashflow
    tx = _seed_income(db_session, user_with_item.items[0], "in_cf", 4000.0)
    db_session.add(TransactionOverride(
        user_id=tx.user_id,
        plaid_transaction_id=tx.plaid_transaction_id,
        amount_override=round(tx.amount * 25 / 100.0, 2),  # = -1000.0
        split_percentage=25.0,
        source="rule",
    ))
    db_session.commit()

    rows = monthly_cashflow(user_with_item, db_session, n_months=1)
    assert len(rows) == 1
    assert rows[0]["income"] == 1000.0


def test_income_split_dollar_override(user_with_item, db_session):
    """split_dollar writes amount_override as a positive magnitude; same total."""
    from income import fetch_last_month
    tx = _seed_income(db_session, user_with_item.items[0], "in_sd", 2000.0)
    db_session.add(TransactionOverride(
        user_id=tx.user_id,
        plaid_transaction_id=tx.plaid_transaction_id,
        amount_override=500.0,
        split_percentage=25.0,
        source="rule",
    ))
    db_session.commit()

    out = fetch_last_month(user_with_item, session=db_session)
    assert out["total"] == 500.0
