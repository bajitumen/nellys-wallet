"""Tests for transaction rules: creation, retroactive apply, sync-time apply."""

from datetime import date
from unittest.mock import MagicMock


def _seed_tx(session, item, plaid_id, amount, name, pfc="FOOD_AND_DRINK", merchant=None):
    from models import Transaction
    session.add(Transaction(
        user_id=item.user_id, item_id=item.id, plaid_transaction_id=plaid_id,
        date=date.today(), amount=amount, name=name,
        merchant_name=merchant if merchant is not None else name,
        pfc_primary=pfc,
    ))
    session.commit()


def test_apply_rule_retroactively_creates_overrides(user_with_item, db_session):
    import rules as rules_mod
    from models import TransactionOverride
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "t1", 25.0, "Venmo")
    _seed_tx(db_session, item, "t2", 18.0, "Venmo")
    _seed_tx(db_session, item, "t3", 9.0, "Coffee Shop")

    rule = rules_mod.upsert_rule(
        user_with_item.id, "merchant_name", "Venmo", "dismiss", None, db_session,
    )
    created = rules_mod.apply_rule_retroactively(rule, db_session)
    db_session.commit()

    assert created == 2
    overrides = db_session.query(TransactionOverride).all()
    assert {o.plaid_transaction_id for o in overrides} == {"t1", "t2"}
    assert all(o.dismissed for o in overrides)


def test_rule_does_not_clobber_existing_overrides(user_with_item, db_session):
    import rules as rules_mod
    from models import TransactionOverride
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "t1", 25.0, "Venmo")
    _seed_tx(db_session, item, "t2", 18.0, "Venmo")
    db_session.add(TransactionOverride(
        user_id=user_with_item.id, plaid_transaction_id="t1",
        category_override="FOOD_AND_DRINK",
    ))
    db_session.commit()

    rule = rules_mod.upsert_rule(
        user_with_item.id, "merchant_name", "Venmo", "dismiss", None, db_session,
    )
    created = rules_mod.apply_rule_retroactively(rule, db_session)
    db_session.commit()

    # Only t2 gets a new override; t1's existing override is preserved.
    assert created == 1
    t1_ov = db_session.query(TransactionOverride).filter_by(plaid_transaction_id="t1").one()
    assert t1_ov.dismissed is False
    assert t1_ov.category_override == "FOOD_AND_DRINK"


def test_rules_apply_at_sync_time(user_with_item, db_session, patch_plaid):
    """A pre-existing rule auto-creates overrides for new synced transactions."""
    import rules as rules_mod
    from models import TransactionOverride
    from spending import sync_transactions
    rules_mod.upsert_rule(
        user_with_item.id, "merchant_name", "Venmo", "dismiss", None, db_session,
    )
    db_session.commit()

    tx = MagicMock()
    tx.transaction_id = "newtx"
    tx.amount = 50.0
    tx.date = date.today()
    tx.name = "VENMO PAYMENT"
    tx.merchant_name = "Venmo"
    tx.pending = False
    tx.pending_transaction_id = None
    pfc = MagicMock()
    pfc.primary = "TRANSFER_OUT"
    pfc.detailed = None
    tx.personal_finance_category = pfc
    resp = MagicMock()
    resp.transactions = [tx]
    patch_plaid.transactions_get.return_value = resp

    sync_transactions(user_with_item, db_session)

    ov = db_session.query(TransactionOverride).filter_by(plaid_transaction_id="newtx").one()
    assert ov.dismissed is True


def test_rule_set_category(user_with_item, db_session):
    """A 'set_category' rule writes category_override and clears detailed."""
    import rules as rules_mod
    from models import TransactionOverride
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "t1", 5.0, "Starbucks")

    rule = rules_mod.upsert_rule(
        user_with_item.id, "merchant_name", "Starbucks",
        "set_category", "FOOD_AND_DRINK", db_session,
    )
    rules_mod.apply_rule_retroactively(rule, db_session)
    db_session.commit()

    ov = db_session.query(TransactionOverride).filter_by(plaid_transaction_id="t1").one()
    assert ov.category_override == "FOOD_AND_DRINK"
    assert ov.detailed_override is None
    assert ov.dismissed is False


def test_match_falls_back_to_name_when_merchant_null(user_with_item, db_session):
    """If a tx has no merchant_name (NULL), rules matching on 'name' still apply."""
    import rules as rules_mod
    from models import Transaction, TransactionOverride
    item = user_with_item.items[0]
    from datetime import date as _date
    db_session.add(Transaction(
        user_id=user_with_item.id, item_id=item.id,
        plaid_transaction_id="t1", date=_date.today(), amount=25.0,
        name="ZELLE FROM JOHN 12345", merchant_name=None,
        pfc_primary="TRANSFER_OUT",
    ))
    db_session.commit()
    rule = rules_mod.upsert_rule(
        user_with_item.id, "name", "ZELLE FROM JOHN 12345",
        "dismiss", None, db_session,
    )
    created = rules_mod.apply_rule_retroactively(rule, db_session)
    db_session.commit()

    assert created == 1
    ov = db_session.query(TransactionOverride).one()
    assert ov.dismissed is True


def test_set_detailed_rule(user_with_item, db_session):
    """A set_detailed rule writes detailed_override and leaves category untouched."""
    import rules as rules_mod
    from models import TransactionOverride
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "t1", 9.0, "Starbucks", pfc="FOOD_AND_DRINK")
    rule = rules_mod.upsert_rule(
        user_with_item.id, "merchant_name", "Starbucks",
        "set_detailed", "FOOD_AND_DRINK_COFFEE", db_session,
    )
    rules_mod.apply_rule_retroactively(rule, db_session)
    db_session.commit()
    ov = db_session.query(TransactionOverride).one()
    assert ov.detailed_override == "FOOD_AND_DRINK_COFFEE"
    assert ov.category_override is None


def test_split_dollar_clamps_to_tx_amount(user_with_item, db_session):
    """split_dollar caps the user's share at the tx's full amount."""
    import rules as rules_mod
    from models import TransactionOverride
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "t1", 40.0, "Roommate")
    rule = rules_mod.upsert_rule(
        user_with_item.id, "merchant_name", "Roommate",
        "split_dollar", "100", db_session,
    )
    rules_mod.apply_rule_retroactively(rule, db_session)
    db_session.commit()
    ov = db_session.query(TransactionOverride).one()
    assert ov.amount_override == 40.0
    assert ov.split_percentage == 100.0


def test_preview_endpoint_counts_matches(client, user_with_item, db_session):
    """POST /rules/preview returns how many txs would match — used for not_equals warnings."""
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "t1", 25.0, "Venmo")
    _seed_tx(db_session, item, "t2", 10.0, "Other1")
    _seed_tx(db_session, item, "t3", 5.0, "Other2")

    # equals: matches only Venmo
    r = client.post("/rules/preview", json={
        "match_field": "merchant_name", "match_op": "equals",
        "match_value": "Venmo", "action": "dismiss",
    })
    assert r.status_code == 200
    assert r.get_json()["matches"] == 1

    # not_equals: matches everything except Venmo
    r = client.post("/rules/preview", json={
        "match_field": "merchant_name", "match_op": "not_equals",
        "match_value": "Venmo", "action": "dismiss",
    })
    assert r.status_code == 200
    assert r.get_json()["matches"] == 2


def test_upsert_dedups_case_insensitively(user_with_item, db_session):
    """Saving 'Venmo' then 'VENMO' as the same field/op/action collapses to one rule."""
    import rules as rules_mod
    from models import TransactionRule
    rules_mod.upsert_rule(
        user_with_item.id, "merchant_name", "Venmo",
        "dismiss", None, db_session,
    )
    rules_mod.upsert_rule(
        user_with_item.id, "merchant_name", "VENMO",
        "dismiss", None, db_session,
    )
    db_session.commit()
    rules = db_session.query(TransactionRule).all()
    assert len(rules) == 1
    # Latest casing wins for display.
    assert rules_mod.rule_conditions(rules[0])[0].match_value == "VENMO"


def test_upsert_same_trigger_updates_action_value_in_place(user_with_item, db_session):
    """Re-upserting a trigger with a new action_value updates the rule, not a duplicate."""
    import rules as rules_mod
    from models import TransactionRule
    rules_mod.upsert_rule(
        user_with_item.id, "merchant_name", "Venmo",
        "split", "50", db_session,
    )
    rules_mod.upsert_rule(
        user_with_item.id, "merchant_name", "Venmo",
        "split", "60", db_session,
    )
    db_session.commit()
    rules = db_session.query(TransactionRule).all()
    assert len(rules) == 1
    assert rules[0].action_value == "60"


def test_invalid_match_value_for_taxonomy_field(client, user_with_item):
    """match_field=pfc_primary with a bogus value is rejected."""
    r = client.post("/rules", json={
        "match_field": "pfc_primary", "match_op": "equals",
        "match_value": "BOGUS_CATEGORY", "action": "dismiss",
    })
    assert r.status_code == 400
    assert "match_value" in r.get_json()["error"]


def test_category_scope_rule(user_with_item, db_session):
    """A rule scoped to category matches all txs of that primary."""
    import rules as rules_mod
    from models import TransactionOverride
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "t1", 25.0, "Random Shop", pfc="ENTERTAINMENT")
    _seed_tx(db_session, item, "t2", 18.0, "Other", pfc="ENTERTAINMENT")
    _seed_tx(db_session, item, "t3", 9.0, "Coffee", pfc="FOOD_AND_DRINK")

    rule = rules_mod.upsert_rule(
        user_with_item.id, "pfc_primary", "ENTERTAINMENT",
        "dismiss", None, db_session,
    )
    created = rules_mod.apply_rule_retroactively(rule, db_session)
    db_session.commit()

    assert created == 2
    dismissed_ids = {
        o.plaid_transaction_id for o in
        db_session.query(TransactionOverride).filter_by(dismissed=True)
    }
    assert dismissed_ids == {"t1", "t2"}


def test_split_rule_sets_amount_from_percentage(user_with_item, db_session):
    """A 'split' rule sets split_percentage and computes amount_override per tx."""
    import rules as rules_mod
    from models import TransactionOverride
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "t1", 100.0, "Roommate")
    _seed_tx(db_session, item, "t2", 50.0, "Roommate")

    rule = rules_mod.upsert_rule(
        user_with_item.id, "merchant_name", "Roommate",
        "split", "50", db_session,
    )
    rules_mod.apply_rule_retroactively(rule, db_session)
    db_session.commit()

    ov1 = db_session.query(TransactionOverride).filter_by(plaid_transaction_id="t1").one()
    ov2 = db_session.query(TransactionOverride).filter_by(plaid_transaction_id="t2").one()
    assert ov1.split_percentage == 50.0
    assert ov1.amount_override == 50.0
    assert ov2.amount_override == 25.0


def test_rules_endpoint_creates_and_applies(client, user_with_item, db_session):
    """POST /rules creates a rule from explicit field/op/value and applies retroactively."""
    from models import TransactionOverride, TransactionRule
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "t1", 25.0, "Venmo")
    _seed_tx(db_session, item, "t2", 18.0, "Venmo")

    r = client.post("/rules", json={
        "match_field": "merchant_name",
        "match_op": "equals",
        "match_value": "Venmo",
        "action": "dismiss",
    })
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["applied_to"] == 2

    rules = db_session.query(TransactionRule).all()
    assert len(rules) == 1
    import rules as rules_mod
    conds = rules_mod.rule_conditions(rules[0])
    assert len(conds) == 1
    assert conds[0].match_field == "merchant_name"
    assert conds[0].match_op == "equals"
    assert conds[0].match_value == "Venmo"
    ovs = db_session.query(TransactionOverride).filter_by(dismissed=True).all()
    assert {o.plaid_transaction_id for o in ovs} == {"t1", "t2"}


def test_not_equals_rule(user_with_item, db_session):
    """A not_equals rule matches every tx whose field differs from the value."""
    import rules as rules_mod
    from models import TransactionOverride
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "t1", 25.0, "Keep")
    _seed_tx(db_session, item, "t2", 18.0, "Other")
    _seed_tx(db_session, item, "t3", 5.0, "Other")

    rule = rules_mod.upsert_rule(
        user_with_item.id, "merchant_name", "Keep",
        "dismiss", None, db_session, match_op="not_equals",
    )
    created = rules_mod.apply_rule_retroactively(rule, db_session)
    db_session.commit()

    assert created == 2
    dismissed = {o.plaid_transaction_id for o in db_session.query(TransactionOverride)
                 .filter_by(dismissed=True)}
    assert dismissed == {"t2", "t3"}


def test_more_specific_rule_wins_at_sync(user_with_item, db_session, patch_plaid):
    """When two rules in the same action group match a new tx, the more specific one wins."""
    import rules as rules_mod
    from models import TransactionOverride
    from spending import sync_transactions
    # Broad rule: dismiss anything in TRANSFER_OUT
    rules_mod.upsert_rule(
        user_with_item.id, "pfc_primary", "TRANSFER_OUT",
        "set_category", "GENERAL_MERCHANDISE", db_session,
    )
    # Specific rule: Venmo gets ENTERTAINMENT category
    rules_mod.upsert_rule(
        user_with_item.id, "merchant_name", "Venmo",
        "set_category", "ENTERTAINMENT", db_session,
    )
    db_session.commit()

    tx = MagicMock()
    tx.transaction_id = "newtx"
    tx.amount = 50.0
    tx.date = date.today()
    tx.name = "VENMO PAYMENT"
    tx.merchant_name = "Venmo"
    tx.pending = False
    tx.pending_transaction_id = None
    pfc = MagicMock()
    pfc.primary = "TRANSFER_OUT"
    pfc.detailed = None
    tx.personal_finance_category = pfc
    resp = MagicMock()
    resp.transactions = [tx]
    patch_plaid.transactions_get.return_value = resp

    sync_transactions(user_with_item, db_session)

    ov = db_session.query(TransactionOverride).filter_by(plaid_transaction_id="newtx").one()
    assert ov.category_override == "ENTERTAINMENT"
    assert ov.source == "rule"


def test_different_action_types_coexist(user_with_item, db_session):
    """A dismiss rule and a set_category rule can both apply to the same tx."""
    import rules as rules_mod
    from models import TransactionOverride
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "t1", 25.0, "Venmo", pfc="TRANSFER_OUT")
    # Broad: dismiss all TRANSFER_OUT
    rules_mod.upsert_rule(
        user_with_item.id, "pfc_primary", "TRANSFER_OUT",
        "dismiss", None, db_session,
    )
    # Specific: set Venmo's category
    venmo_rule = rules_mod.upsert_rule(
        user_with_item.id, "merchant_name", "Venmo",
        "set_category", "ENTERTAINMENT", db_session,
    )
    rules_mod.apply_rule_retroactively(venmo_rule, db_session)
    db_session.commit()

    ov = db_session.query(TransactionOverride).one()
    assert ov.dismissed is True
    assert ov.category_override == "ENTERTAINMENT"


def test_manual_override_protected_from_rules(user_with_item, db_session):
    """Once a user manually edits an override, rules don't touch it."""
    import rules as rules_mod
    from models import TransactionOverride
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "t1", 25.0, "Venmo")
    db_session.add(TransactionOverride(
        user_id=user_with_item.id, plaid_transaction_id="t1",
        category_override="TRAVEL", source="manual",
    ))
    db_session.commit()

    rule = rules_mod.upsert_rule(
        user_with_item.id, "merchant_name", "Venmo",
        "set_category", "ENTERTAINMENT", db_session,
    )
    rules_mod.apply_rule_retroactively(rule, db_session)
    db_session.commit()

    ov = db_session.query(TransactionOverride).one()
    assert ov.category_override == "TRAVEL"  # manual untouched


def test_more_specific_rule_supersedes_earlier_rule_override(user_with_item, db_session):
    """Adding a more specific rule rewrites rule-sourced overrides; manual still protected."""
    import rules as rules_mod
    from models import TransactionOverride
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "t1", 25.0, "Venmo", pfc="TRANSFER_OUT")
    broad = rules_mod.upsert_rule(
        user_with_item.id, "pfc_primary", "TRANSFER_OUT",
        "set_category", "GENERAL_MERCHANDISE", db_session,
    )
    rules_mod.apply_rule_retroactively(broad, db_session)
    db_session.commit()
    ov = db_session.query(TransactionOverride).one()
    assert ov.category_override == "GENERAL_MERCHANDISE"
    assert ov.source == "rule"

    specific = rules_mod.upsert_rule(
        user_with_item.id, "merchant_name", "Venmo",
        "set_category", "ENTERTAINMENT", db_session,
    )
    rules_mod.apply_rule_retroactively(specific, db_session)
    db_session.commit()
    ov = db_session.query(TransactionOverride).one()
    assert ov.category_override == "ENTERTAINMENT"


def test_create_rule_is_idempotent_on_identical_payload(client, user_with_item, db_session):
    """Posting the same rule payload twice creates exactly one rule.

    Guards the timeout-retry case where the client aborts but the server
    already committed: a follow-up retry must not duplicate.
    """
    from models import TransactionRule
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "t1", 25.0, "Venmo")
    payload = {
        "conditions": [
            {"match_field": "merchant_name", "match_op": "equals", "match_value": "Venmo"},
        ],
        "conditions_logic": "all",
        "action": "dismiss",
        "scope": "spending",
    }
    r1 = client.post("/rules", json=payload)
    r2 = client.post("/rules", json=payload)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.get_json()["rule_id"] == r2.get_json()["rule_id"]
    assert db_session.query(TransactionRule).count() == 1


def test_create_rule_is_idempotent_regardless_of_match_value_case(client, user_with_item, db_session):
    """Same logical rule with different casing on match_value still dedupes."""
    from models import TransactionRule
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "t1", 25.0, "Venmo")
    r1 = client.post("/rules", json={
        "conditions": [{"match_field": "merchant_name", "match_op": "equals", "match_value": "Venmo"}],
        "conditions_logic": "all", "action": "dismiss", "scope": "spending",
    })
    r2 = client.post("/rules", json={
        "conditions": [{"match_field": "merchant_name", "match_op": "equals", "match_value": "VENMO"}],
        "conditions_logic": "all", "action": "dismiss", "scope": "spending",
    })
    assert r1.get_json()["rule_id"] == r2.get_json()["rule_id"]
    assert db_session.query(TransactionRule).count() == 1


def test_create_rule_different_logic_is_separate(client, user_with_item, db_session):
    """Same conditions but different conditions_logic is NOT a duplicate."""
    from models import TransactionRule
    conds = [
        {"match_field": "merchant_name", "match_op": "equals", "match_value": "Venmo"},
        {"match_field": "pfc_primary", "match_op": "equals", "match_value": "FOOD_AND_DRINK"},
    ]
    client.post("/rules", json={"conditions": conds, "conditions_logic": "all",
                                 "action": "dismiss", "scope": "all"})
    client.post("/rules", json={"conditions": conds, "conditions_logic": "any",
                                 "action": "dismiss", "scope": "all"})
    assert db_session.query(TransactionRule).count() == 2


def test_edit_with_failing_reapply_rolls_back_rule_edit(client, user_with_item, db_session, monkeypatch):
    """If reapply_after_edit fails the rule edit must roll back too.

    Otherwise the user has a rule that disagrees with the overrides it left
    behind, and nothing in the codebase ever reconciles them.
    """
    import rules as rules_mod
    from models import TransactionRule
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "t1", 25.0, "Starbucks")

    rule = rules_mod.create_rule(
        user_with_item.id,
        [{"match_field": "merchant_name", "match_op": "equals", "match_value": "Starbucks"}],
        "all", "dismiss", None, "all", db_session,
    )
    db_session.commit()
    original_value = rule.conditions[0].match_value

    def boom(*args, **kwargs):
        raise RuntimeError("apply blew up")
    monkeypatch.setattr(rules_mod, "reapply_after_edit", boom)

    r = client.post("/rules", json={
        "rule_id": rule.id,
        "conditions": [{"match_field": "merchant_name", "match_op": "equals", "match_value": "Peets"}],
        "conditions_logic": "all",
        "action": "dismiss",
        "scope": "all",
    })
    assert r.status_code == 500

    db_session.expire_all()
    after = db_session.query(TransactionRule).filter_by(id=rule.id).one()
    assert after.conditions[0].match_value == original_value, \
        "rule edit must roll back when reapply fails"


def test_rules_endpoint_rejects_non_integer_rule_id(client, user_with_item, db_session):
    """rule_id='abc' returns 400, not 500 from int() throwing."""
    r = client.post("/rules", json={
        "rule_id": "not-an-int",
        "conditions": [{"match_field": "merchant_name", "match_op": "equals", "match_value": "X"}],
        "conditions_logic": "all", "action": "dismiss", "scope": "all",
    })
    assert r.status_code == 400


def test_null_field_does_not_match_not_equals_rule(user_with_item, db_session):
    """A tx with NULL merchant_name must NOT match `merchant_name != X`.

    SQL `lower(NULL) != x` is NULL (excluded); Python must agree, otherwise the
    same rule applies at sync time but not on retro-apply.
    """
    import rules as rules_mod
    from models import Transaction, TransactionOverride
    item = user_with_item.items[0]
    # merchant_name is NULL; name is populated.
    db_session.add(Transaction(
        user_id=user_with_item.id, item_id=item.id, plaid_transaction_id="t1",
        date=date.today(), amount=10.0, name="ATM WITHDRAWAL",
        merchant_name=None, pfc_primary="GENERAL_MERCHANDISE",
    ))
    db_session.commit()

    # In-memory matcher: NULL merchant_name should NOT satisfy != "Starbucks".
    cond = rules_mod._PayloadCondition("merchant_name", "not_equals", "Starbucks")
    tx = db_session.query(Transaction).one()
    assert rules_mod._condition_matches_tx(tx, cond) is False

    # End-to-end: rule with merchant_name != Starbucks should leave the tx alone.
    rule = rules_mod.create_rule(
        user_with_item.id,
        [{"match_field": "merchant_name", "match_op": "not_equals", "match_value": "Starbucks"}],
        "all", "dismiss", None, "all", db_session,
    )
    rules_mod.apply_rule_retroactively(rule, db_session)
    db_session.commit()
    assert db_session.query(TransactionOverride).count() == 0


def test_delete_rule_clears_rule_sourced_overrides(user_with_item, db_session):
    """Deleting a rule must clear the source='rule' overrides it created."""
    import rules as rules_mod
    from models import TransactionOverride
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "t1", 25.0, "Venmo")
    _seed_tx(db_session, item, "t2", 10.0, "Venmo")

    rule = rules_mod.create_rule(
        user_with_item.id,
        [{"match_field": "merchant_name", "match_op": "equals", "match_value": "Venmo"}],
        "all", "dismiss", None, "all", db_session,
    )
    rules_mod.apply_rule_retroactively(rule, db_session)
    db_session.commit()
    assert db_session.query(TransactionOverride).filter_by(dismissed=True).count() == 2

    rules_mod.delete_rule(user_with_item, rule.id, db_session)
    db_session.commit()
    assert db_session.query(TransactionOverride).count() == 0


def test_delete_rule_preserves_manual_overrides(user_with_item, db_session):
    """Manual overrides survive rule deletion even when they overlap the rule's scope."""
    import rules as rules_mod
    from models import TransactionOverride
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "t1", 25.0, "Venmo")
    db_session.add(TransactionOverride(
        user_id=user_with_item.id, plaid_transaction_id="t1",
        category_override="ENTERTAINMENT", source="manual",
    ))
    db_session.commit()

    rule = rules_mod.create_rule(
        user_with_item.id,
        [{"match_field": "merchant_name", "match_op": "equals", "match_value": "Venmo"}],
        "all", "dismiss", None, "all", db_session,
    )
    db_session.commit()  # rule exists but won't touch the manual override
    rules_mod.delete_rule(user_with_item, rule.id, db_session)
    db_session.commit()

    ov = db_session.query(TransactionOverride).one()
    assert ov.source == "manual"
    assert ov.category_override == "ENTERTAINMENT"


def test_delete_rule_does_not_clear_overrides_from_another_rule(user_with_item, db_session):
    """Two rules dismiss the same tx; deleting one keeps the other's effect."""
    import rules as rules_mod
    from models import TransactionOverride
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "t1", 25.0, "Venmo", pfc="TRANSFER_OUT")

    r_merchant = rules_mod.create_rule(
        user_with_item.id,
        [{"match_field": "merchant_name", "match_op": "equals", "match_value": "Venmo"}],
        "all", "dismiss", None, "all", db_session,
    )
    rules_mod.create_rule(
        user_with_item.id,
        [{"match_field": "pfc_primary", "match_op": "equals", "match_value": "TRANSFER_OUT"}],
        "all", "dismiss", None, "all", db_session,
    )
    rules_mod.apply_rule_retroactively(r_merchant, db_session)
    db_session.commit()
    assert db_session.query(TransactionOverride).filter_by(dismissed=True).count() == 1

    rules_mod.delete_rule(user_with_item, r_merchant.id, db_session)
    db_session.commit()
    ov = db_session.query(TransactionOverride).one()
    assert ov.dismissed is True  # other rule still in play


def test_winning_rules_ties_break_by_id(user_with_item, db_session):
    """On equal specificity the older rule wins (deterministic)."""
    import rules as rules_mod
    from models import TransactionOverride
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "t1", 10.0, "Same", pfc="FOOD_AND_DRINK")

    older = rules_mod.create_rule(
        user_with_item.id,
        [{"match_field": "merchant_name", "match_op": "equals", "match_value": "Same"}],
        "all", "set_category", "GENERAL_MERCHANDISE", "all", db_session,
    )
    db_session.commit()
    rules_mod.create_rule(
        user_with_item.id,
        [{"match_field": "merchant_name", "match_op": "equals", "match_value": "Same"}],
        "all", "set_category", "ENTERTAINMENT", "all", db_session,
    )
    db_session.commit()
    rules_mod.apply_rule_retroactively(older, db_session)
    db_session.commit()
    ov = db_session.query(TransactionOverride).one()
    assert ov.category_override == "GENERAL_MERCHANDISE"  # older wins, stable


def test_spending_scoped_rule_does_not_touch_income_tx(user_with_item, db_session):
    """A rule with scope='spending' must not match income transactions."""
    import rules as rules_mod
    from models import Transaction, TransactionOverride
    item = user_with_item.items[0]
    db_session.add_all([
        # Venmo outflow (spending): amount > 0, TRANSFER_OUT
        Transaction(
            user_id=item.user_id, item_id=item.id, plaid_transaction_id="out1",
            date=date.today(), amount=30.0, name="Venmo", merchant_name="Venmo",
            pfc_primary="TRANSFER_OUT",
        ),
        # Venmo inflow (income): amount < 0, TRANSFER_IN
        Transaction(
            user_id=item.user_id, item_id=item.id, plaid_transaction_id="in1",
            date=date.today(), amount=-500.0, name="Venmo", merchant_name="Venmo",
            pfc_primary="TRANSFER_IN",
        ),
    ])
    db_session.commit()

    rule = rules_mod.upsert_rule(
        user_with_item.id, "merchant_name", "Venmo", "dismiss", None, db_session,
        scope="spending",
    )
    rules_mod.apply_rule_retroactively(rule, db_session)
    db_session.commit()

    ovs = {o.plaid_transaction_id: o for o in db_session.query(TransactionOverride)}
    assert "out1" in ovs and ovs["out1"].dismissed is True
    assert "in1" not in ovs, "income Venmo tx must not be touched by spending-scoped rule"


def test_income_scoped_rule_does_not_touch_spending_tx(user_with_item, db_session):
    """A rule with scope='income' must not match spending transactions."""
    import rules as rules_mod
    from models import Transaction, TransactionOverride
    item = user_with_item.items[0]
    db_session.add_all([
        Transaction(
            user_id=item.user_id, item_id=item.id, plaid_transaction_id="out1",
            date=date.today(), amount=30.0, name="Acme Payroll", merchant_name="Acme Payroll",
            pfc_primary="GENERAL_MERCHANDISE",
        ),
        Transaction(
            user_id=item.user_id, item_id=item.id, plaid_transaction_id="in1",
            date=date.today(), amount=-2500.0, name="Acme Payroll", merchant_name="Acme Payroll",
            pfc_primary="INCOME",
        ),
    ])
    db_session.commit()

    rule = rules_mod.upsert_rule(
        user_with_item.id, "merchant_name", "Acme Payroll", "dismiss", None, db_session,
        scope="income",
    )
    rules_mod.apply_rule_retroactively(rule, db_session)
    db_session.commit()

    ovs = {o.plaid_transaction_id: o for o in db_session.query(TransactionOverride)}
    assert "in1" in ovs and ovs["in1"].dismissed is True
    assert "out1" not in ovs


def test_scope_filtered_at_sync_time(user_with_item, db_session, patch_plaid):
    """A spending-scoped rule must not auto-dismiss a new income tx at sync."""
    import rules as rules_mod
    from models import TransactionOverride
    from spending import sync_transactions
    rules_mod.upsert_rule(
        user_with_item.id, "merchant_name", "Venmo", "dismiss", None, db_session,
        scope="spending",
    )
    db_session.commit()

    income_tx = MagicMock()
    income_tx.transaction_id = "in_new"
    income_tx.amount = -200.0
    income_tx.date = date.today()
    income_tx.name = "VENMO FROM JOHN"
    income_tx.merchant_name = "Venmo"
    income_tx.pending = False
    income_tx.pending_transaction_id = None
    pfc = MagicMock()
    pfc.primary = "TRANSFER_IN"
    pfc.detailed = None
    income_tx.personal_finance_category = pfc
    resp = MagicMock()
    resp.transactions = [income_tx]
    patch_plaid.transactions_get.return_value = resp

    sync_transactions(user_with_item, db_session)

    assert db_session.query(TransactionOverride).filter_by(
        plaid_transaction_id="in_new"
    ).one_or_none() is None


def test_same_merchant_can_have_distinct_spending_and_income_rules(user_with_item, db_session):
    """Two rules with the same field/value/action but different scopes are independent."""
    import rules as rules_mod
    from models import TransactionRule
    spending = rules_mod.upsert_rule(
        user_with_item.id, "merchant_name", "Venmo", "dismiss", None, db_session,
        scope="spending",
    )
    income = rules_mod.upsert_rule(
        user_with_item.id, "merchant_name", "Venmo", "dismiss", None, db_session,
        scope="income",
    )
    db_session.commit()
    assert spending.id != income.id
    assert db_session.query(TransactionRule).count() == 2


def test_spending_scope_excludes_null_pfc_primary_at_sync_time(
    user_with_item, db_session, patch_plaid,
):
    """Sync-time path must skip a NULL-pfc_primary tx for spending scope,
    matching the SQL filter used by retroactive apply and by the spending page."""
    import rules as rules_mod
    from models import TransactionOverride
    from spending import sync_transactions
    rules_mod.upsert_rule(
        user_with_item.id, "merchant_name", "Mystery", "dismiss", None, db_session,
        scope="spending",
    )
    db_session.commit()

    tx = MagicMock()
    tx.transaction_id = "null_cat"
    tx.amount = 10.0
    tx.date = date.today()
    tx.name = "MYSTERY CHARGE"
    tx.merchant_name = "Mystery"
    tx.pending = False
    tx.pending_transaction_id = None
    tx.personal_finance_category = None
    resp = MagicMock()
    resp.transactions = [tx]
    patch_plaid.transactions_get.return_value = resp

    sync_transactions(user_with_item, db_session)

    assert db_session.query(TransactionOverride).filter_by(
        plaid_transaction_id="null_cat"
    ).one_or_none() is None


def test_editing_rule_clears_stale_overrides_on_old_side(user_with_item, db_session):
    """Editing a scope='all' rule to scope='spending' must clear overrides on
    the income txs that the rule used to dismiss."""
    import rules as rules_mod
    from models import Transaction, TransactionOverride, TransactionRule
    item = user_with_item.items[0]
    db_session.add_all([
        Transaction(
            user_id=item.user_id, item_id=item.id, plaid_transaction_id="spend1",
            date=date.today(), amount=30.0, name="Venmo", merchant_name="Venmo",
            pfc_primary="TRANSFER_OUT",
        ),
        Transaction(
            user_id=item.user_id, item_id=item.id, plaid_transaction_id="inc1",
            date=date.today(), amount=-500.0, name="Venmo", merchant_name="Venmo",
            pfc_primary="TRANSFER_IN",
        ),
    ])
    db_session.commit()

    rule = rules_mod.upsert_rule(
        user_with_item.id, "merchant_name", "Venmo", "dismiss", None, db_session,
        scope="all",
    )
    rules_mod.apply_rule_retroactively(rule, db_session)
    db_session.commit()

    ovs = {o.plaid_transaction_id: o for o in db_session.query(TransactionOverride)}
    assert ovs["spend1"].dismissed is True
    assert ovs["inc1"].dismissed is True

    old_txs = rules_mod.snapshot_rule_txs(rule, db_session)
    rule.scope = "spending"
    rules_mod.reapply_after_edit(rule, old_txs, db_session)
    db_session.commit()

    ovs = {o.plaid_transaction_id: o for o in db_session.query(TransactionOverride)}
    assert "spend1" in ovs and ovs["spend1"].dismissed is True
    assert "inc1" not in ovs, "income override should be cleared once rule no longer covers it"


def test_editing_rule_preserves_manual_overrides(user_with_item, db_session):
    """Editing a rule must not touch source='manual' overrides, even on the
    old-matched side."""
    import rules as rules_mod
    from models import Transaction, TransactionOverride
    item = user_with_item.items[0]
    db_session.add_all([
        Transaction(
            user_id=item.user_id, item_id=item.id, plaid_transaction_id="inc1",
            date=date.today(), amount=-500.0, name="Venmo", merchant_name="Venmo",
            pfc_primary="TRANSFER_IN",
        ),
    ])
    db_session.add(TransactionOverride(
        user_id=user_with_item.id, plaid_transaction_id="inc1",
        category_override="INCOME", source="manual",
    ))
    db_session.commit()

    rule = rules_mod.upsert_rule(
        user_with_item.id, "merchant_name", "Venmo", "dismiss", None, db_session,
        scope="all",
    )
    old_txs = rules_mod.snapshot_rule_txs(rule, db_session)
    rule.scope = "spending"
    rules_mod.reapply_after_edit(rule, old_txs, db_session)
    db_session.commit()

    ov = db_session.query(TransactionOverride).filter_by(plaid_transaction_id="inc1").one()
    assert ov.source == "manual"
    assert ov.category_override == "INCOME"
    assert ov.dismissed is False


def test_delete_rule(user_with_item, db_session):
    import rules as rules_mod
    from models import TransactionRule
    rule = rules_mod.upsert_rule(
        user_with_item.id, "merchant_name", "Venmo", "dismiss", None, db_session,
    )
    db_session.commit()
    assert db_session.query(TransactionRule).count() == 1
    rules_mod.delete_rule(user_with_item, rule.id, db_session)
    db_session.commit()
    assert db_session.query(TransactionRule).count() == 0


# ---------------------------------------------------------------------------
# Multi-condition rules
# ---------------------------------------------------------------------------

def test_all_logic_requires_every_condition_to_match(user_with_item, db_session):
    import rules as rules_mod
    from models import TransactionOverride
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "t1", 20.0, "Starbucks", pfc="FOOD_AND_DRINK")
    _seed_tx(db_session, item, "t2", 20.0, "Starbucks", pfc="ENTERTAINMENT")
    _seed_tx(db_session, item, "t3", 20.0, "Other", pfc="FOOD_AND_DRINK")

    rule = rules_mod.create_rule(
        user_with_item.id,
        [
            {"match_field": "merchant_name", "match_op": "equals", "match_value": "Starbucks"},
            {"match_field": "pfc_primary", "match_op": "equals", "match_value": "FOOD_AND_DRINK"},
        ],
        "all", "dismiss", None, "all", db_session,
    )
    rules_mod.apply_rule_retroactively(rule, db_session)
    db_session.commit()
    dismissed = {o.plaid_transaction_id for o in db_session.query(TransactionOverride).filter_by(dismissed=True)}
    assert dismissed == {"t1"}


def test_any_logic_matches_when_either_condition_matches(user_with_item, db_session):
    import rules as rules_mod
    from models import TransactionOverride
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "t1", 20.0, "Starbucks", pfc="FOOD_AND_DRINK")
    _seed_tx(db_session, item, "t2", 20.0, "Other", pfc="ENTERTAINMENT")
    _seed_tx(db_session, item, "t3", 20.0, "Other", pfc="FOOD_AND_DRINK")

    rule = rules_mod.create_rule(
        user_with_item.id,
        [
            {"match_field": "merchant_name", "match_op": "equals", "match_value": "Starbucks"},
            {"match_field": "pfc_primary", "match_op": "equals", "match_value": "FOOD_AND_DRINK"},
        ],
        "any", "dismiss", None, "all", db_session,
    )
    rules_mod.apply_rule_retroactively(rule, db_session)
    db_session.commit()
    dismissed = {o.plaid_transaction_id for o in db_session.query(TransactionOverride).filter_by(dismissed=True)}
    assert dismissed == {"t1", "t3"}


def test_multi_condition_payload_endpoint(client, user_with_item, db_session):
    """POST /rules with conditions array creates a multi-clause rule and applies it."""
    from models import TransactionRule, TransactionOverride
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "t1", 20.0, "Starbucks", pfc="FOOD_AND_DRINK")
    _seed_tx(db_session, item, "t2", 20.0, "Starbucks", pfc="ENTERTAINMENT")

    r = client.post("/rules", json={
        "conditions": [
            {"match_field": "merchant_name", "match_op": "equals", "match_value": "Starbucks"},
            {"match_field": "pfc_primary", "match_op": "equals", "match_value": "FOOD_AND_DRINK"},
        ],
        "conditions_logic": "all",
        "action": "dismiss",
        "scope": "spending",
    })
    assert r.status_code == 200
    body = r.get_json()
    assert body["applied_to"] == 1

    rule = db_session.query(TransactionRule).one()
    assert rule.conditions_logic == "all"
    assert len(rule.conditions) == 2
    dismissed = {o.plaid_transaction_id for o in db_session.query(TransactionOverride).filter_by(dismissed=True)}
    assert dismissed == {"t1"}


def test_legacy_single_condition_payload_still_accepted(client, user_with_item, db_session):
    """Old shape ({"match_field": ..., "match_value": ...}) keeps working."""
    from models import TransactionRule
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "t1", 20.0, "Venmo")
    r = client.post("/rules", json={
        "match_field": "merchant_name",
        "match_op": "equals",
        "match_value": "Venmo",
        "action": "dismiss",
        "scope": "spending",
    })
    assert r.status_code == 200
    rule = db_session.query(TransactionRule).one()
    assert len(rule.conditions) == 1
    assert rule.conditions[0].match_value == "Venmo"


def test_edit_rule_replaces_conditions(client, user_with_item, db_session):
    """Editing a multi-condition rule swaps in the new condition set."""
    import rules as rules_mod
    from models import TransactionRule
    rule = rules_mod.create_rule(
        user_with_item.id,
        [{"match_field": "merchant_name", "match_op": "equals", "match_value": "Old"}],
        "all", "dismiss", None, "all", db_session,
    )
    db_session.commit()

    r = client.post("/rules", json={
        "rule_id": rule.id,
        "conditions": [
            {"match_field": "merchant_name", "match_op": "equals", "match_value": "New"},
            {"match_field": "pfc_primary", "match_op": "not_equals", "match_value": "FOOD_AND_DRINK"},
        ],
        "conditions_logic": "any",
        "action": "dismiss",
        "scope": "all",
    })
    assert r.status_code == 200
    db_session.refresh(rule)
    assert rule.conditions_logic == "any"
    assert len(rule.conditions) == 2
    values = {c.match_value for c in rule.conditions}
    assert values == {"New", "FOOD_AND_DRINK"}


def test_source_condition_matches_only_that_institutions_txs(user_with_item, db_session):
    """A source=Other rule must not touch txs from a different institution."""
    import rules as rules_mod
    from models import PlaidItem, Transaction, TransactionOverride
    other = PlaidItem(user_id=user_with_item.id, institution_name="OtherBank")
    other.set_access_token("access-other")
    db_session.add(other)
    db_session.commit()

    main_item = user_with_item.items[0]
    other_item = db_session.query(PlaidItem).filter_by(institution_name="OtherBank").one()
    db_session.add_all([
        Transaction(
            user_id=user_with_item.id, item_id=main_item.id, plaid_transaction_id="t_main",
            date=date.today(), amount=10.0, name="Coffee", merchant_name="Coffee",
            pfc_primary="FOOD_AND_DRINK",
        ),
        Transaction(
            user_id=user_with_item.id, item_id=other_item.id, plaid_transaction_id="t_other",
            date=date.today(), amount=10.0, name="Coffee", merchant_name="Coffee",
            pfc_primary="FOOD_AND_DRINK",
        ),
    ])
    db_session.commit()

    rule = rules_mod.create_rule(
        user_with_item.id,
        [{"match_field": "source", "match_op": "equals", "match_value": "OtherBank"}],
        "all", "dismiss", None, "all", db_session,
    )
    rules_mod.apply_rule_retroactively(rule, db_session)
    db_session.commit()
    dismissed = {o.plaid_transaction_id for o in db_session.query(TransactionOverride).filter_by(dismissed=True)}
    assert dismissed == {"t_other"}


def test_source_condition_combined_with_merchant_all_logic(user_with_item, db_session):
    """source=X AND merchant=Y only dismisses txs that satisfy both."""
    import rules as rules_mod
    from models import PlaidItem, Transaction, TransactionOverride
    other = PlaidItem(user_id=user_with_item.id, institution_name="OtherBank")
    other.set_access_token("access-other")
    db_session.add(other)
    db_session.commit()
    other_item = db_session.query(PlaidItem).filter_by(institution_name="OtherBank").one()
    main_item = user_with_item.items[0]

    db_session.add_all([
        Transaction(
            user_id=user_with_item.id, item_id=other_item.id, plaid_transaction_id="t1",
            date=date.today(), amount=10.0, name="Venmo", merchant_name="Venmo",
            pfc_primary="TRANSFER_OUT",
        ),
        Transaction(
            user_id=user_with_item.id, item_id=other_item.id, plaid_transaction_id="t2",
            date=date.today(), amount=10.0, name="Coffee", merchant_name="Coffee",
            pfc_primary="FOOD_AND_DRINK",
        ),
        Transaction(
            user_id=user_with_item.id, item_id=main_item.id, plaid_transaction_id="t3",
            date=date.today(), amount=10.0, name="Venmo", merchant_name="Venmo",
            pfc_primary="TRANSFER_OUT",
        ),
    ])
    db_session.commit()

    rule = rules_mod.create_rule(
        user_with_item.id,
        [
            {"match_field": "source", "match_op": "equals", "match_value": "OtherBank"},
            {"match_field": "merchant_name", "match_op": "equals", "match_value": "Venmo"},
        ],
        "all", "dismiss", None, "all", db_session,
    )
    rules_mod.apply_rule_retroactively(rule, db_session)
    db_session.commit()
    dismissed = {o.plaid_transaction_id for o in db_session.query(TransactionOverride).filter_by(dismissed=True)}
    assert dismissed == {"t1"}


def test_source_condition_via_endpoint(client, user_with_item, db_session):
    """POST /rules accepts a source condition."""
    from models import TransactionRule
    r = client.post("/rules", json={
        "conditions": [
            {"match_field": "source", "match_op": "equals", "match_value": "TestBank"},
        ],
        "conditions_logic": "all",
        "action": "dismiss",
        "scope": "all",
    })
    assert r.status_code == 200
    rule = db_session.query(TransactionRule).one()
    assert rule.conditions[0].match_field == "source"
    assert rule.conditions[0].match_value == "TestBank"


def test_multi_condition_preview_count(client, user_with_item, db_session):
    """Preview returns the same count the rule would apply to."""
    item = user_with_item.items[0]
    _seed_tx(db_session, item, "t1", 20.0, "Starbucks", pfc="FOOD_AND_DRINK")
    _seed_tx(db_session, item, "t2", 20.0, "Other", pfc="FOOD_AND_DRINK")
    _seed_tx(db_session, item, "t3", 20.0, "Starbucks", pfc="ENTERTAINMENT")

    r = client.post("/rules/preview", json={
        "conditions": [
            {"match_field": "merchant_name", "match_op": "equals", "match_value": "Starbucks"},
            {"match_field": "pfc_primary", "match_op": "equals", "match_value": "FOOD_AND_DRINK"},
        ],
        "conditions_logic": "any",
        "action": "dismiss",
        "scope": "spending",
    })
    assert r.status_code == 200
    assert r.get_json()["matches"] == 3
