"""Tests for the internal-transfer pair matcher and its effect on filters."""

from datetime import date, timedelta


_INTERNAL_DETAILED = {
    "TRANSFER_OUT": "TRANSFER_OUT_ACCOUNT_TRANSFER",
    "TRANSFER_IN": "TRANSFER_IN_ACCOUNT_TRANSFER",
}


def _seed_tx(session, item, plaid_id, amount, name, pfc, date_=None, detailed=None):
    from models import Transaction
    # Default to the internal-transfer detailed code so pre-existing pair tests
    # remain valid — pair_internal_transfers now requires the detailed code to
    # mark a transfer as own-account. Tests that intentionally seed an
    # external transfer (Zelle to a friend) pass detailed="" or a non-internal code.
    if detailed is None and pfc in _INTERNAL_DETAILED:
        detailed = _INTERNAL_DETAILED[pfc]
    session.add(Transaction(
        user_id=item.user_id, item_id=item.id, plaid_transaction_id=plaid_id,
        date=date_ or date.today(),
        amount=amount, name=name, merchant_name=name,
        pfc_primary=pfc,
        pfc_detailed=detailed,
    ))
    session.commit()


def _add_second_item(db_session, user):
    """Give the user a second linked institution for cross-bank pair tests."""
    from models import PlaidItem
    item = PlaidItem(user_id=user.id, institution_name="OtherBank")
    item.set_access_token("access-other")
    db_session.add(item)
    db_session.commit()
    db_session.refresh(user)
    return item


def test_pair_matches_cross_bank_same_amount_same_day(user_with_item, db_session):
    import transfers as transfers_mod
    from models import Transaction
    other = _add_second_item(db_session, user_with_item)
    today = date.today()
    _seed_tx(db_session, user_with_item.items[0], "out1",
             500.0, "Transfer to Chase", "TRANSFER_OUT", date_=today)
    _seed_tx(db_session, other, "in1",
             -500.0, "Transfer from BoA", "TRANSFER_IN", date_=today)

    flagged = transfers_mod.pair_internal_transfers(user_with_item.id, db_session)
    assert flagged == 2
    flags = {t.plaid_transaction_id: t.is_internal_transfer
             for t in db_session.query(Transaction).all()}
    assert flags == {"out1": True, "in1": True}


def test_pair_matches_same_bank_internal_transfer(user_with_item, db_session):
    """BoA checking -> BoA savings: same item_id on both legs, must still pair."""
    import transfers as transfers_mod
    from models import Transaction
    item = user_with_item.items[0]
    today = date.today()
    _seed_tx(db_session, item, "out1",
             300.0, "Transfer to Savings", "TRANSFER_OUT", date_=today)
    _seed_tx(db_session, item, "in1",
             -300.0, "Transfer from Checking", "TRANSFER_IN", date_=today)

    flagged = transfers_mod.pair_internal_transfers(user_with_item.id, db_session)
    assert flagged == 2
    flags = {t.plaid_transaction_id: t.is_internal_transfer
             for t in db_session.query(Transaction).all()}
    assert flags == {"out1": True, "in1": True}


def test_pair_matches_within_window(user_with_item, db_session):
    import transfers as transfers_mod
    from models import Transaction
    other = _add_second_item(db_session, user_with_item)
    today = date.today()
    _seed_tx(db_session, user_with_item.items[0], "out1",
             120.0, "Out", "TRANSFER_OUT", date_=today - timedelta(days=2))
    _seed_tx(db_session, other, "in1",
             -120.0, "In", "TRANSFER_IN", date_=today)

    transfers_mod.pair_internal_transfers(user_with_item.id, db_session)
    flags = {t.plaid_transaction_id: t.is_internal_transfer
             for t in db_session.query(Transaction).all()}
    assert flags == {"out1": True, "in1": True}


def test_pair_skips_outside_window(user_with_item, db_session):
    import transfers as transfers_mod
    from models import Transaction
    other = _add_second_item(db_session, user_with_item)
    today = date.today()
    _seed_tx(db_session, user_with_item.items[0], "out1",
             200.0, "Out", "TRANSFER_OUT", date_=today - timedelta(days=10))
    _seed_tx(db_session, other, "in1",
             -200.0, "In", "TRANSFER_IN", date_=today)

    transfers_mod.pair_internal_transfers(user_with_item.id, db_session)
    flags = {t.plaid_transaction_id: t.is_internal_transfer
             for t in db_session.query(Transaction).all()}
    assert flags == {"out1": False, "in1": False}


def test_pair_does_not_match_zelle_to_friend(user_with_item, db_session):
    """A solo TRANSFER_OUT with no corresponding TRANSFER_IN must stay unpaired."""
    import transfers as transfers_mod
    from models import Transaction
    _seed_tx(db_session, user_with_item.items[0], "zelle1",
             75.0, "Zelle to Alice", "TRANSFER_OUT")
    transfers_mod.pair_internal_transfers(user_with_item.id, db_session)
    flags = {t.plaid_transaction_id: t.is_internal_transfer
             for t in db_session.query(Transaction).all()}
    assert flags == {"zelle1": False}


def test_pair_does_not_match_when_amounts_differ(user_with_item, db_session):
    import transfers as transfers_mod
    from models import Transaction
    other = _add_second_item(db_session, user_with_item)
    today = date.today()
    _seed_tx(db_session, user_with_item.items[0], "out1",
             100.0, "Out", "TRANSFER_OUT", date_=today)
    _seed_tx(db_session, other, "in1",
             -100.01, "In", "TRANSFER_IN", date_=today)
    transfers_mod.pair_internal_transfers(user_with_item.id, db_session)
    assert not any(t.is_internal_transfer for t in db_session.query(Transaction).all())


def test_each_tx_pairs_at_most_once(user_with_item, db_session):
    """Two outs of $50 + one in of $50: only one pair forms."""
    import transfers as transfers_mod
    from models import Transaction
    other = _add_second_item(db_session, user_with_item)
    today = date.today()
    _seed_tx(db_session, user_with_item.items[0], "out1", 50.0, "Out1", "TRANSFER_OUT", today)
    _seed_tx(db_session, user_with_item.items[0], "out2", 50.0, "Out2", "TRANSFER_OUT", today)
    _seed_tx(db_session, other, "in1", -50.0, "In1", "TRANSFER_IN", today)

    flagged = transfers_mod.pair_internal_transfers(user_with_item.id, db_session)
    assert flagged == 2
    paired = {t.plaid_transaction_id for t in db_session.query(Transaction)
              if t.is_internal_transfer}
    assert len(paired) == 2
    assert "in1" in paired  # the IN is consumed
    assert ("out1" in paired) != ("out2" in paired)  # exactly one OUT


def test_pair_idempotent_outcome(user_with_item, db_session):
    """Re-running rebuilds the pair from scratch but the final flags don't change.

    (We no longer return 0 on a re-run — the matcher clears stale flags first so
    a Plaid recategorization on a later sync can't leave a phantom pair stuck.
    What matters is that the post-state is identical.)
    """
    import transfers as transfers_mod
    from models import Transaction
    other = _add_second_item(db_session, user_with_item)
    today = date.today()
    _seed_tx(db_session, user_with_item.items[0], "out1", 30.0, "Out", "TRANSFER_OUT", today)
    _seed_tx(db_session, other, "in1", -30.0, "In", "TRANSFER_IN", today)
    transfers_mod.pair_internal_transfers(user_with_item.id, db_session)
    transfers_mod.pair_internal_transfers(user_with_item.id, db_session)
    flags = {t.plaid_transaction_id: t.is_internal_transfer
             for t in db_session.query(Transaction).all()}
    assert flags == {"out1": True, "in1": True}


def test_pair_clears_stale_flag_on_recategorization(user_with_item, db_session):
    """If a tx gets a new pfc_detailed on a later sync that's no longer an
    internal-transfer code, its prior is_internal_transfer=True must drop."""
    import transfers as transfers_mod
    from models import Transaction
    other = _add_second_item(db_session, user_with_item)
    today = date.today()
    _seed_tx(db_session, user_with_item.items[0], "out1", 30.0, "Out", "TRANSFER_OUT", today)
    _seed_tx(db_session, other, "in1", -30.0, "In", "TRANSFER_IN", today)
    transfers_mod.pair_internal_transfers(user_with_item.id, db_session)
    # Simulate Plaid recategorizing the IN leg as an external deposit.
    in_row = db_session.query(Transaction).filter_by(plaid_transaction_id="in1").one()
    in_row.pfc_detailed = "TRANSFER_IN_DEPOSIT"
    db_session.flush()
    transfers_mod.pair_internal_transfers(user_with_item.id, db_session)
    flags = {t.plaid_transaction_id: t.is_internal_transfer
             for t in db_session.query(Transaction).all()}
    assert flags == {"out1": False, "in1": False}


def test_pair_clears_stale_flag_when_primary_changes(user_with_item, db_session):
    """If a previously-paired IN leg gets its pfc_primary rewritten (Plaid
    recategorizes from TRANSFER_IN to INCOME after the bank tags it as payroll),
    the stale is_internal_transfer=True must drop — otherwise that income
    silently disappears from every total forever.
    """
    import transfers as transfers_mod
    from models import Transaction
    from income import fetch_last_month
    other = _add_second_item(db_session, user_with_item)
    today = date.today()
    _seed_tx(db_session, user_with_item.items[0], "out1", 500.0, "Out",
             "TRANSFER_OUT", today)
    _seed_tx(db_session, other, "in1", -500.0, "Acme Payroll",
             "TRANSFER_IN", today)
    transfers_mod.pair_internal_transfers(user_with_item.id, db_session)
    # Plaid recategorizes the IN leg as INCOME on a later sync — different
    # pfc_primary, so the row leaves the TRANSFER_* candidate set.
    in_row = db_session.query(Transaction).filter_by(plaid_transaction_id="in1").one()
    in_row.pfc_primary = "INCOME"
    in_row.pfc_detailed = "INCOME_WAGES"
    db_session.flush()

    transfers_mod.pair_internal_transfers(user_with_item.id, db_session)

    refreshed = db_session.query(Transaction).filter_by(plaid_transaction_id="in1").one()
    assert refreshed.is_internal_transfer is False, (
        "primary-rewritten transfer kept stale internal flag — income vanished"
    )
    out = fetch_last_month(user_with_item, session=db_session)
    assert out["total"] == 500.0


def test_pair_skips_external_transfer_with_matching_amount(user_with_item, db_session):
    """A Zelle-out + a same-amount external deposit on the same day must NOT pair.

    Without the detailed-code constraint, two unrelated $100 movements get
    falsely paired and both vanish from spending and income.
    """
    import transfers as transfers_mod
    from models import Transaction
    today = date.today()
    _seed_tx(db_session, user_with_item.items[0], "zelle_out",
             100.0, "Zelle to Alice", "TRANSFER_OUT", today,
             detailed="TRANSFER_OUT_OTHER_TRANSFER_OUT")
    _seed_tx(db_session, user_with_item.items[0], "ext_in",
             -100.0, "Refund", "TRANSFER_IN", today,
             detailed="TRANSFER_IN_DEPOSIT")
    transfers_mod.pair_internal_transfers(user_with_item.id, db_session)
    flags = {t.plaid_transaction_id: t.is_internal_transfer
             for t in db_session.query(Transaction).all()}
    assert flags == {"zelle_out": False, "ext_in": False}


def test_spending_excludes_paired_transfers(user_with_item, db_session):
    """Paired internal transfers always drop from spending; Zelle to a friend stays."""
    import transfers as transfers_mod
    from spending import fetch_last_month
    other = _add_second_item(db_session, user_with_item)
    today = date.today()
    _seed_tx(db_session, user_with_item.items[0], "internal_out",
             200.0, "to OtherBank", "TRANSFER_OUT", today)
    _seed_tx(db_session, other, "internal_in",
             -200.0, "from BoA", "TRANSFER_IN", today)
    _seed_tx(db_session, user_with_item.items[0], "zelle_out",
             40.0, "Zelle to Alice", "TRANSFER_OUT", today)
    _seed_tx(db_session, user_with_item.items[0], "lunch",
             18.0, "Cafe", "FOOD_AND_DRINK", today)
    transfers_mod.pair_internal_transfers(user_with_item.id, db_session)
    db_session.commit()

    out = fetch_last_month(user_with_item, session=db_session)
    ids = {tx["plaid_id"] for tx in out["transactions"]}
    assert "internal_out" not in ids  # internal transfer dropped
    assert "zelle_out" in ids  # Zelle to friend kept
    assert "lunch" in ids


def test_income_excludes_paired_transfers_and_unpaired_transfer_in(user_with_item, db_session):
    """Income surface is strict — only INCOME counts. Both paired internal
    legs and standalone TRANSFER_IN (Zelle from a friend, loan disbursement)
    must drop out."""
    import transfers as transfers_mod
    from income import fetch_last_month
    other = _add_second_item(db_session, user_with_item)
    today = date.today()
    _seed_tx(db_session, user_with_item.items[0], "out_leg",
             400.0, "to Chase", "TRANSFER_OUT", today)
    _seed_tx(db_session, other, "in_leg",
             -400.0, "from BoA", "TRANSFER_IN", today)
    _seed_tx(db_session, user_with_item.items[0], "venmo_in",
             -25.0, "Venmo from Bob", "TRANSFER_IN", today)
    _seed_tx(db_session, user_with_item.items[0], "wages",
             -2500.0, "Acme Payroll", "INCOME", today)
    transfers_mod.pair_internal_transfers(user_with_item.id, db_session)
    db_session.commit()

    out = fetch_last_month(user_with_item, session=db_session)
    ids = {tx["plaid_id"] for tx in out["transactions"]}
    assert "in_leg" not in ids
    assert "venmo_in" not in ids
    assert "wages" in ids
    assert out["total"] == 2500.0
