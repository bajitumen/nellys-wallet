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
VALID_SCOPES = frozenset({"all", "spending", "income"})

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
    if rule.scope in ("spending", "income"):
        return rule.scope
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


def _tx_in_scope(tx: Transaction, scope: str) -> bool:
    if scope == "all":
        return True
    if scope == "spending":
        # Mirror the SQL filter (NULL pfc_primary excluded), so sync-time and
        # retroactive paths can't disagree on a NULL-category tx.
        return (
            tx.amount is not None
            and tx.amount > 0
            and tx.pfc_primary is not None
            and tx.pfc_primary not in INCOME_PRIMARIES
        )
    if scope == "income":
        return (
            tx.amount is not None
            and tx.amount < 0
            and tx.pfc_primary in INCOME_PRIMARIES
        )
    return False


def _build_scope_filter(scope: str):
    if scope == "spending":
        return [Transaction.amount > 0, ~Transaction.pfc_primary.in_(INCOME_PRIMARIES)]
    if scope == "income":
        return [Transaction.amount < 0, Transaction.pfc_primary.in_(INCOME_PRIMARIES)]
    return []


def _tx_matches_rule(tx: Transaction, rule: TransactionRule) -> bool:
    if not _tx_in_scope(tx, rule.scope):
        return False
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
    scope: str = "all",
) -> TransactionRule:
    # Case-insensitive lookup so "Venmo" and "venmo" don't create separate rules.
    # Storage keeps the caller's casing for display. Scope is part of identity:
    # the same merchant can have separate 'spending' and 'income' rules.
    rule = (
        session.query(TransactionRule)
        .filter(
            TransactionRule.user_id == user_id,
            TransactionRule.match_field == match_field,
            TransactionRule.match_op == match_op,
            func.lower(TransactionRule.match_value) == match_value.lower(),
            TransactionRule.action == action,
            TransactionRule.scope == scope,
        )
        .one_or_none()
    )
    if rule is None:
        rule = TransactionRule(
            user_id=user_id, match_field=match_field, match_op=match_op,
            match_value=match_value, action=action, action_value=action_value,
            scope=scope,
        )
        session.add(rule)
    else:
        rule.action_value = action_value
        rule.match_value = match_value
    session.flush()
    return rule


def _build_match_filter(col, op: str, value: str):
    lowered = value.lower()
    if op == "not_equals":
        return func.lower(col) != lowered
    return func.lower(col) == lowered


def _query_txs_for_criteria(
    user_id: int, match_field: str, match_op: str, match_value: str, scope: str, session,
) -> list[Transaction]:
    col = _MATCH_COLUMNS.get(match_field)
    if col is None:
        return []
    return (
        session.query(Transaction)
        .filter(
            Transaction.user_id == user_id,
            _build_match_filter(col, match_op, match_value),
            *_build_scope_filter(scope),
        )
        .all()
    )


def _recompute_overrides_for_txs(user_id: int, txs: list[Transaction], session) -> int:
    if not txs:
        return 0
    all_rules = (
        session.query(TransactionRule).filter_by(user_id=user_id).all()
    )
    tx_ids = [t.plaid_transaction_id for t in txs]
    existing_by_id = {
        o.plaid_transaction_id: o for o in
        session.query(TransactionOverride)
        .filter(
            TransactionOverride.user_id == user_id,
            TransactionOverride.plaid_transaction_id.in_(tx_ids),
        )
    }

    affected = 0
    for tx in txs:
        existing = existing_by_id.get(tx.plaid_transaction_id)
        # Manual protection is all-or-nothing: any manual touch on a tx blocks
        # all rule application on that tx.
        if existing is not None and existing.source == "manual":
            continue
        matching_for_tx = [r for r in all_rules if _tx_matches_rule(tx, r)]
        winners = _winning_rules(matching_for_tx)
        if not winners:
            if existing is not None and existing.source == "rule":
                session.delete(existing)
                affected += 1
            continue
        if existing is None:
            ov = TransactionOverride(
                user_id=user_id,
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


def apply_rule_retroactively(rule: TransactionRule, session) -> int:
    txs = _query_txs_for_criteria(
        rule.user_id, rule.match_field, rule.match_op,
        rule.match_value, rule.scope, session,
    )
    return _recompute_overrides_for_txs(rule.user_id, txs, session)


def reapply_after_edit(
    rule: TransactionRule, old_criteria: tuple[str, str, str, str], session,
) -> int:
    new_txs = _query_txs_for_criteria(
        rule.user_id, rule.match_field, rule.match_op,
        rule.match_value, rule.scope, session,
    )
    old_field, old_op, old_value, old_scope = old_criteria
    old_txs = _query_txs_for_criteria(
        rule.user_id, old_field, old_op, old_value, old_scope, session,
    )
    by_id: dict[int, Transaction] = {}
    for t in new_txs:
        by_id[t.id] = t
    for t in old_txs:
        by_id.setdefault(t.id, t)
    return _recompute_overrides_for_txs(rule.user_id, list(by_id.values()), session)


def apply_rules_to_new_transactions(
    user_id: int, new_txs: list[Transaction], session,
) -> int:
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
    def _distinct(col, order=None):
        return [
            v for (v,) in
            session.query(col)
            .filter(Transaction.user_id == user.id, col.isnot(None))
            .distinct()
            .order_by(order if order is not None else col)
            .all()
            if v
        ]

    merchants = _distinct(Transaction.merchant_name, func.lower(Transaction.merchant_name))
    # Fall back to raw name where merchant_name is null.
    names = [
        v for (v,) in
        session.query(Transaction.name)
        .filter(
            Transaction.user_id == user.id,
            Transaction.name.isnot(None),
            Transaction.merchant_name.is_(None),
        )
        .distinct()
        .order_by(func.lower(Transaction.name))
        .all()
        if v
    ]
    primaries = _distinct(Transaction.pfc_primary)
    detaileds = _distinct(Transaction.pfc_detailed)
    return {
        "merchant": (
            [{"field": "merchant_name", "value": m, "label": m} for m in merchants]
            + [{"field": "name", "value": n, "label": n} for n in names]
        ),
        "category": [{"field": "pfc_primary", "value": p} for p in primaries],
        "item": [{"field": "pfc_detailed", "value": d} for d in detaileds],
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
