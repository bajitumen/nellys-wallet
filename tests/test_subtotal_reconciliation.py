"""Reconciliation tests: subtotals must sum to their parent totals.

Audit gap: a set_detailed rule producing a detailed code rooted in a
different primary than the resolved category landed in a (primary, detailed)
cell that the page's subitem iterator never visited — the amount showed up
in the primary total but in no subitem row.
"""

from datetime import date

from models import Transaction


def test_set_detailed_override_endpoint_rejects_cross_primary(client, user_with_item, db_session):
    """The /transactions/<tx>/override endpoint refuses a detailed code rooted
    in a different primary than the resolved category — the resulting subtotal
    would be unreconciled."""
    import pfc
    tx = Transaction(
        user_id=user_with_item.id,
        item_id=user_with_item.items[0].id,
        plaid_transaction_id="tx_cross",
        date=date.today(),
        amount=10.0,
        name="Coffee",
        pfc_primary="FOOD_AND_DRINK",
    )
    db_session.add(tx)
    db_session.commit()

    # FOOD_AND_DRINK_COFFEE is rooted in FOOD_AND_DRINK; pick a detailed code
    # from a different primary.
    cross_detailed = next(
        d for d in pfc._VALID_DETAILED
        if pfc.primary_of(d) != "FOOD_AND_DRINK"
    )

    r = client.post(
        "/transactions/tx_cross/override",
        json={"category": "FOOD_AND_DRINK", "detailed": cross_detailed},
    )
    assert r.status_code == 400


def test_set_detailed_rule_drops_cross_primary_writes(user_with_item, db_session):
    """A set_detailed rule whose action_value lives under a different primary
    than the tx's resolved category must not write the override at all."""
    import pfc
    import rules as rules_mod
    from models import TransactionOverride

    db_session.add(Transaction(
        user_id=user_with_item.id, item_id=user_with_item.items[0].id,
        plaid_transaction_id="tx_food",
        date=date.today(),
        amount=10.0, name="Coffee", merchant_name="Coffee",
        pfc_primary="FOOD_AND_DRINK",
    ))
    db_session.commit()

    cross_detailed = next(
        d for d in pfc._VALID_DETAILED
        if pfc.primary_of(d) != "FOOD_AND_DRINK"
    )
    rule = rules_mod.upsert_rule(
        user_with_item.id, "merchant_name", "Coffee",
        "set_detailed", cross_detailed, db_session,
    )
    rules_mod.apply_rule_retroactively(rule, db_session)
    db_session.commit()

    ov = (
        db_session.query(TransactionOverride)
        .filter_by(plaid_transaction_id="tx_food")
        .one_or_none()
    )
    if ov is not None:
        assert ov.detailed_override is None, (
            f"cross-primary detailed_override={ov.detailed_override!r} would "
            "be unreconciled in subtotals"
        )


def test_networth_series_excludes_other_bucket(db_session, user_with_item):
    """Per-institution series must apply the same bucket filter as the headline
    net (cash + investments - credit, exclude 'other'); otherwise stacked
    series don't sum to the net line."""
    from datetime import datetime, timedelta, timezone
    from models import AccountBalanceSnapshot, NetWorthSnapshot
    import networth

    base = datetime.now(timezone.utc) - timedelta(days=1)
    db_session.add(NetWorthSnapshot(
        user_id=user_with_item.id, taken_at=base.replace(tzinfo=None),
        cash_total=1000.0, investment_total=500.0, credit_total=100.0,
        net_worth=1400.0,
    ))
    db_session.add_all([
        AccountBalanceSnapshot(
            user_id=user_with_item.id, item_id=user_with_item.items[0].id,
            plaid_account_id="acct_cash", account_name="Checking",
            institution_name="Bank", bucket="cash", balance=1000.0,
            taken_at=base.replace(tzinfo=None),
        ),
        AccountBalanceSnapshot(
            user_id=user_with_item.id, item_id=user_with_item.items[0].id,
            plaid_account_id="acct_inv", account_name="Brokerage",
            institution_name="Bank", bucket="investment", balance=500.0,
            taken_at=base.replace(tzinfo=None),
        ),
        AccountBalanceSnapshot(
            user_id=user_with_item.id, item_id=user_with_item.items[0].id,
            plaid_account_id="acct_other", account_name="Loan",
            institution_name="Bank", bucket="other", balance=999999.0,
            taken_at=base.replace(tzinfo=None),
        ),
    ])
    db_session.commit()

    series = networth.build_series_data(
        networth.get_snapshots(user_with_item, db_session),
        networth.get_account_snapshots(user_with_item, db_session),
    )
    inst_value = series["inst:Bank"][0]["value"]
    net_value = series["net"][0]["value"]
    # inst sum (1500) ≠ net (1400) — credit is excluded from per-institution
    # by design, so they only need to match in the no-credit case. The point
    # of the assertion is that the 'other' bucket isn't being silently rolled
    # in (which would have made inst_value 1001500.0).
    assert inst_value == 1500.0, (
        f"per-institution series picked up the 'other' bucket: {inst_value}"
    )
    assert net_value == 1400.0
