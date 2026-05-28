from sqlalchemy import func

import pfc as pfc_mod
from models import Transaction, TransactionOverride, TransactionRule, User


_MATCH_COLUMNS = {
    "merchant_name": Transaction.merchant_name,
    "name": Transaction.name,
    "pfc_primary": Transaction.pfc_primary,
    "pfc_detailed": Transaction.pfc_detailed,
}

VALID_MATCH_FIELDS = frozenset(_MATCH_COLUMNS.keys())
VALID_MATCH_OPS = frozenset({"equals", "not_equals"})
VALID_ACTIONS = frozenset({"dismiss", "split", "split_dollar", "set_category", "set_detailed"})

_FIELD_SPECIFICITY = {
    "merchant_name": 4,
    "name": 3,
    "pfc_detailed": 2,
    "pfc_primary": 1,
}


def scope_label(rule: TransactionRule) -> str:
    if rule.match_field in ("merchant_name", "name"):
        return "merchant"
    if rule.match_field == "pfc_primary":
        return "category"
    if rule.match_field == "pfc_detailed":
        return "item"
    return rule.match_field


def op_label(rule: TransactionRule) -> str:
    return "is" if rule.match_op == "equals" else "is not"


INCOME_PRIMARIES = frozenset({"INCOME", "TRANSFER_IN"})


def rule_side(rule: TransactionRule) -> str:
    """Classify a rule as 'spending', 'income', or 'both'.

    A category/item rule's side comes from its match value's primary. A
    merchant/name rule with a categorizing action takes its side from the
    action's target. Everything else (merchant + dismiss/split) is 'both' —
    we can't know which page's transactions it'll touch.
    """
    if rule.match_field == "pfc_primary":
        return "income" if rule.match_value in INCOME_PRIMARIES else "spending"
    if rule.match_field == "pfc_detailed":
        primary = pfc_mod.primary_of(rule.match_value)
        return "income" if primary in INCOME_PRIMARIES else "spending"
    if rule.action == "set_category":
        return "income" if rule.action_value in INCOME_PRIMARIES else "spending"
    if rule.action == "set_detailed":
        primary = pfc_mod.primary_of(rule.action_value or "")
        return "income" if primary in INCOME_PRIMARIES else "spending"
    return "both"


def action_label(rule: TransactionRule) -> str:
    if rule.action == "dismiss":
        return "dismiss"
    if rule.action == "set_category":
        return f"categorize as {pfc_mod.humanize_primary(rule.action_value)}"
    if rule.action == "set_detailed":
        return f"label as {pfc_mod.humanize_detailed(rule.action_value)}"
    if rule.action == "split":
        return f"split — my share {rule.action_value}%"
    if rule.action == "split_dollar":
        return f"split — my share ${rule.action_value}"
    return rule.action


def rule_specificity(rule: TransactionRule) -> int:
    """Higher = more specific. 'equals' beats 'not_equals'; field rank breaks ties."""
    field = _FIELD_SPECIFICITY.get(rule.match_field, 0)
    op = 10 if rule.match_op == "equals" else 0
    return op + field


def _action_group(action: str) -> str:
    if action in ("split", "split_dollar"):
        return "split"
    return action


def _tx_matches_rule(tx: Transaction, rule: TransactionRule) -> bool:
    v = getattr(tx, rule.match_field, None)
    v_lc = v.lower() if v else ""
    target = (rule.match_value or "").lower()
    if rule.match_op == "not_equals":
        return v_lc != target
    return v_lc == target


def _winning_rules(matched: list[TransactionRule]) -> list[TransactionRule]:
    """Per action group, keep only the most specific matching rule."""
    by_group: dict[str, TransactionRule] = {}
    for r in matched:
        key = _action_group(r.action)
        cur = by_group.get(key)
        if cur is None or rule_specificity(r) > rule_specificity(cur):
            by_group[key] = r
    return list(by_group.values())


def _reset_rule_fields(ov: TransactionOverride) -> None:
    ov.dismissed = False
    ov.category_override = None
    ov.detailed_override = None
    ov.amount_override = None
    ov.split_percentage = None


def _match_key_for_tx(tx: Transaction) -> tuple[str, str] | None:
    if tx.merchant_name:
        return ("merchant_name", tx.merchant_name)
    if tx.name:
        return ("name", tx.name)
    return None


def _rule_label(tx: Transaction) -> str:
    return tx.merchant_name or tx.name or ""


def _apply_rule_to_override(
    rule: TransactionRule, ov: TransactionOverride, tx: Transaction | None = None,
) -> None:
    if rule.action == "dismiss":
        ov.dismissed = True
    elif rule.action == "set_category":
        ov.category_override = rule.action_value
        ov.detailed_override = None
    elif rule.action == "set_detailed":
        ov.detailed_override = rule.action_value
    elif rule.action == "split":
        # action_value is the user's share as a percentage string (e.g. "50").
        try:
            pct = float(rule.action_value or 0)
        except (TypeError, ValueError):
            return
        ov.split_percentage = pct
        if tx is not None and tx.amount is not None:
            ov.amount_override = round(tx.amount * pct / 100.0, 2)
    elif rule.action == "split_dollar":
        # action_value is a flat dollar amount the user owes for each matched tx.
        try:
            amt = float(rule.action_value or 0)
        except (TypeError, ValueError):
            return
        if tx is not None and tx.amount is not None:
            amt = min(amt, abs(tx.amount))
            if tx.amount:
                ov.split_percentage = round(amt / abs(tx.amount) * 100.0, 2)
        ov.amount_override = amt


def upsert_rule(
    user_id: int, match_field: str, match_value: str, action: str,
    action_value: str | None, session, match_op: str = "equals",
) -> TransactionRule:
    # Case-insensitive lookup so "Venmo" and "venmo" don't create separate rules.
    # Storage keeps the caller's casing for display.
    rule = (
        session.query(TransactionRule)
        .filter(
            TransactionRule.user_id == user_id,
            TransactionRule.match_field == match_field,
            TransactionRule.match_op == match_op,
            func.lower(TransactionRule.match_value) == match_value.lower(),
            TransactionRule.action == action,
        )
        .one_or_none()
    )
    if rule is None:
        rule = TransactionRule(
            user_id=user_id, match_field=match_field, match_op=match_op,
            match_value=match_value, action=action, action_value=action_value,
        )
        session.add(rule)
    else:
        rule.action_value = action_value
        # Refresh stored casing to the latest input.
        rule.match_value = match_value
    session.flush()
    return rule


def _build_match_filter(col, op: str, value: str):
    lowered = value.lower()
    if op == "not_equals":
        return func.lower(col) != lowered
    return func.lower(col) == lowered


def apply_rule_retroactively(rule: TransactionRule, session) -> int:
    """Recompute overrides for every tx matching `rule`, using full rule set + specificity.

    Skips txs whose existing override is source='manual'. Returns count of overrides
    created or modified.
    """
    col = _MATCH_COLUMNS.get(rule.match_field)
    if col is None:
        return 0
    matched_txs = (
        session.query(Transaction)
        .filter(
            Transaction.user_id == rule.user_id,
            _build_match_filter(col, rule.match_op, rule.match_value),
        )
        .all()
    )
    if not matched_txs:
        return 0

    all_rules = (
        session.query(TransactionRule).filter_by(user_id=rule.user_id).all()
    )
    tx_ids = [t.plaid_transaction_id for t in matched_txs]
    existing_by_id = {
        o.plaid_transaction_id: o for o in
        session.query(TransactionOverride)
        .filter(
            TransactionOverride.user_id == rule.user_id,
            TransactionOverride.plaid_transaction_id.in_(tx_ids),
        )
    }

    affected = 0
    for tx in matched_txs:
        existing = existing_by_id.get(tx.plaid_transaction_id)
        # Manual protection is all-or-nothing: any manual touch on a tx blocks
        # all rule application on that tx (every field), not just the field the
        # user edited. Keeps the rule path simple; future improvement is
        # per-field provenance if this proves too coarse.
        if existing is not None and existing.source == "manual":
            continue
        matching_for_tx = [r for r in all_rules if _tx_matches_rule(tx, r)]
        winners = _winning_rules(matching_for_tx)
        # The current rule matches `tx` by construction, so `winners` is always
        # non-empty here.
        if existing is None:
            ov = TransactionOverride(
                user_id=rule.user_id,
                plaid_transaction_id=tx.plaid_transaction_id,
                source="rule",
            )
            session.add(ov)
        else:
            ov = existing
            _reset_rule_fields(ov)
            ov.source = "rule"
        for r in winners:
            _apply_rule_to_override(r, ov, tx)
        affected += 1
    return affected


def apply_rules_to_new_transactions(
    user_id: int, new_txs: list[Transaction], session,
) -> int:
    """At sync time: auto-create overrides for new tx rows matching saved rules.

    Skip txs that already have an override (per-tx override wins).
    Returns count of overrides created.
    """
    if not new_txs:
        return 0
    rules = session.query(TransactionRule).filter_by(user_id=user_id).all()
    if not rules:
        return 0

    tx_ids = [t.plaid_transaction_id for t in new_txs]
    existing = {
        o.plaid_transaction_id for o in
        session.query(TransactionOverride.plaid_transaction_id)
        .filter(
            TransactionOverride.user_id == user_id,
            TransactionOverride.plaid_transaction_id.in_(tx_ids),
        )
    }

    created = 0
    for tx in new_txs:
        if tx.plaid_transaction_id in existing:
            continue
        matched = [r for r in rules if _tx_matches_rule(tx, r)]
        if not matched:
            continue
        winners = _winning_rules(matched)
        ov = TransactionOverride(
            user_id=user_id,
            plaid_transaction_id=tx.plaid_transaction_id,
            source="rule",
        )
        for r in winners:
            _apply_rule_to_override(r, ov, tx)
        session.add(ov)
        created += 1
    return created


def list_rules(user: User, session) -> list[TransactionRule]:
    return (
        session.query(TransactionRule)
        .filter_by(user_id=user.id)
        .order_by(TransactionRule.created_at.desc())
        .all()
    )


def user_match_options(user: User, session) -> dict:
    """Distinct merchants, categories, and items the user has transactions for.

    Used by the rule-builder modal to populate value dropdowns.
    """
    merchants = [
        (m,) for (m,) in
        session.query(Transaction.merchant_name)
        .filter(Transaction.user_id == user.id, Transaction.merchant_name.isnot(None))
        .distinct()
        .order_by(func.lower(Transaction.merchant_name))
        .all()
        if m
    ]
    # Fall back to raw name where merchant_name is null.
    names = [
        (n,) for (n,) in
        session.query(Transaction.name)
        .filter(
            Transaction.user_id == user.id,
            Transaction.name.isnot(None),
            Transaction.merchant_name.is_(None),
        )
        .distinct()
        .order_by(func.lower(Transaction.name))
        .all()
        if n
    ]
    primaries = [
        (p,) for (p,) in
        session.query(Transaction.pfc_primary)
        .filter(Transaction.user_id == user.id, Transaction.pfc_primary.isnot(None))
        .distinct()
        .order_by(Transaction.pfc_primary)
        .all()
        if p
    ]
    detaileds = [
        (d,) for (d,) in
        session.query(Transaction.pfc_detailed)
        .filter(Transaction.user_id == user.id, Transaction.pfc_detailed.isnot(None))
        .distinct()
        .order_by(Transaction.pfc_detailed)
        .all()
        if d
    ]
    return {
        "merchant": (
            [{"field": "merchant_name", "value": m[0], "label": m[0]} for m in merchants]
            + [{"field": "name", "value": n[0], "label": n[0]} for n in names]
        ),
        "category": [{"field": "pfc_primary", "value": p[0]} for p in primaries],
        "item": [{"field": "pfc_detailed", "value": d[0]} for d in detaileds],
    }


def delete_rule(user: User, rule_id: int, session) -> bool:
    row = (
        session.query(TransactionRule)
        .filter_by(id=rule_id, user_id=user.id)
        .one_or_none()
    )
    if row is None:
        return False
    session.delete(row)
    return True
