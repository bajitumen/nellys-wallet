from sqlalchemy import and_, false, func, or_, true

import pfc as pfc_mod
from models import (
    PlaidItem,
    Transaction,
    TransactionOverride,
    TransactionRule,
    TransactionRuleCondition,
    User,
)


_MATCH_COLUMNS = {
    "merchant_name": Transaction.merchant_name,
    "name": Transaction.name,
    "pfc_primary": Transaction.pfc_primary,
    "pfc_detailed": Transaction.pfc_detailed,
}

# `source` is a virtual field resolved via PlaidItem.institution_name, not a column
# on Transaction. Match paths special-case it.
VALID_MATCH_FIELDS = frozenset(set(_MATCH_COLUMNS.keys()) | {"source"})
VALID_MATCH_OPS = frozenset({"equals", "not_equals"})
VALID_ACTIONS = frozenset({"dismiss", "split", "split_dollar", "set_category", "set_detailed"})
VALID_SCOPES = frozenset({"all", "spending", "income"})
VALID_LOGIC = frozenset({"all", "any"})

_FIELD_SPECIFICITY = {
    "merchant_name": 4,
    "name": 3,
    "pfc_detailed": 2,
    "source": 1,
    "pfc_primary": 1,
}

INCOME_PRIMARIES = frozenset({"INCOME", "TRANSFER_IN"})


class _LegacyCondition:
    __slots__ = ("match_field", "match_op", "match_value")

    def __init__(self, field, op, value):
        self.match_field = field
        self.match_op = op or "equals"
        self.match_value = value


def rule_conditions(rule: TransactionRule) -> list:
    """Conditions list; falls back to the rule's legacy match_* fields if empty."""
    if rule.conditions:
        return list(rule.conditions)
    if rule.match_field and rule.match_value is not None:
        return [_LegacyCondition(rule.match_field, rule.match_op, rule.match_value)]
    return []


def _condition_label_field(field: str) -> str:
    if field in ("merchant_name", "name"):
        return "merchant"
    if field == "pfc_primary":
        return "category"
    if field == "pfc_detailed":
        return "item"
    if field == "source":
        return "source"
    return field


def condition_labels(rule: TransactionRule) -> list[dict]:
    """Human-readable rendering of each condition for templates."""
    out: list[dict] = []
    for c in rule_conditions(rule):
        if c.match_field == "pfc_primary":
            value = pfc_mod.humanize_primary(c.match_value)
        elif c.match_field == "pfc_detailed":
            value = pfc_mod.humanize_detailed(c.match_value)
        else:
            value = c.match_value
        out.append({
            "scope_label": _condition_label_field(c.match_field),
            "op_label": "is" if c.match_op == "equals" else "is not",
            "match_value": value,
        })
    return out


def _item_institutions(user_id: int, session) -> dict[int, str]:
    return {
        i: (name or "Unknown") for (i, name) in
        session.query(PlaidItem.id, PlaidItem.institution_name)
        .filter_by(user_id=user_id).all()
    }


def _resolve_source_item_ids(user_id: int, value: str, session) -> set[int]:
    target = (value or "").lower()
    return {
        i for i, name in _item_institutions(user_id, session).items()
        if name.lower() == target
    }


def rule_side(rule: TransactionRule) -> str:
    if rule.scope in ("spending", "income"):
        return rule.scope
    conds = rule_conditions(rule)
    for c in conds:
        if c.match_field == "pfc_primary":
            return "income" if c.match_value in INCOME_PRIMARIES else "spending"
        if c.match_field == "pfc_detailed":
            primary = pfc_mod.primary_of(c.match_value)
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
    """Higher = more specific. Rule's max condition specificity wins."""
    conds = rule_conditions(rule)
    if not conds:
        return 0
    return max(
        _FIELD_SPECIFICITY.get(c.match_field, 0)
        + (10 if c.match_op == "equals" else 0)
        for c in conds
    )


def _action_group(action: str) -> str:
    if action in ("split", "split_dollar"):
        return "split"
    return action


def _tx_in_scope(tx: Transaction, scope: str) -> bool:
    if scope == "all":
        return True
    if scope == "spending":
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


def _condition_matches_tx(tx: Transaction, cond, item_institutions=None) -> bool:
    # Align with SQL semantics: NULL on either side means "unknown" and never
    # satisfies an equality test (in either direction). Without this, retro-apply
    # (SQL) and sync-time apply (Python) disagree on NULL-field txs, so the same
    # rule can match a tx in one path and skip it in the other.
    if cond.match_field == "source":
        if tx.item_id is None:
            return False
        v = (item_institutions or {}).get(tx.item_id)
    else:
        v = getattr(tx, cond.match_field, None)
    if v is None:
        return False
    target = (cond.match_value or "").lower()
    v_lc = v.lower()
    if cond.match_op == "not_equals":
        return v_lc != target
    return v_lc == target


def _tx_matches_rule(tx: Transaction, rule: TransactionRule, item_institutions=None) -> bool:
    if not _tx_in_scope(tx, rule.scope):
        return False
    conds = rule_conditions(rule)
    if not conds:
        return False
    matches = (_condition_matches_tx(tx, c, item_institutions) for c in conds)
    return any(matches) if rule.conditions_logic == "any" else all(matches)


def _winning_rules(matched: list[TransactionRule]) -> list[TransactionRule]:
    """Per action group, keep only the most specific matching rule.

    Tie-break is deterministic: on equal specificity the older rule wins
    (lower id). Without this, the result depends on row order from the DB
    and the same tx can flap between rules across syncs.
    """
    def rank(r: TransactionRule) -> tuple:
        # Higher specificity sorts first; older id breaks ties.
        return (-rule_specificity(r), r.id if r.id is not None else 0)

    by_group: dict[str, TransactionRule] = {}
    for r in sorted(matched, key=rank):
        key = _action_group(r.action)
        if key not in by_group:
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
        try:
            pct = float(rule.action_value or 0)
        except (TypeError, ValueError):
            return
        ov.split_percentage = pct
        if tx is not None and tx.amount is not None:
            ov.amount_override = round(tx.amount * pct / 100.0, 2)
    elif rule.action == "split_dollar":
        try:
            amt = float(rule.action_value or 0)
        except (TypeError, ValueError):
            return
        if tx is not None and tx.amount is not None:
            amt = min(amt, abs(tx.amount))
            if tx.amount:
                ov.split_percentage = round(amt / abs(tx.amount) * 100.0, 2)
        ov.amount_override = amt


def _build_match_filter(col, op: str, value: str):
    lowered = value.lower()
    if op == "not_equals":
        return func.lower(col) != lowered
    return func.lower(col) == lowered


def _build_conditions_filter(conditions, logic: str, user_id: int, session):
    clauses = []
    for c in conditions:
        if c.match_field == "source":
            ids = _resolve_source_item_ids(user_id, c.match_value, session)
            if c.match_op == "not_equals":
                clauses.append(~Transaction.item_id.in_(ids) if ids else true())
            else:
                clauses.append(Transaction.item_id.in_(ids) if ids else false())
            continue
        col = _MATCH_COLUMNS.get(c.match_field)
        if col is None:
            continue
        clauses.append(_build_match_filter(col, c.match_op, c.match_value))
    if not clauses:
        return None
    return or_(*clauses) if logic == "any" else and_(*clauses)


def _query_txs_for_rule(rule: TransactionRule, session) -> list[Transaction]:
    conds = rule_conditions(rule)
    cond_filter = _build_conditions_filter(conds, rule.conditions_logic, rule.user_id, session)
    if cond_filter is None:
        return []
    return (
        session.query(Transaction)
        .filter(
            Transaction.user_id == rule.user_id,
            cond_filter,
            *_build_scope_filter(rule.scope),
        )
        .all()
    )


def _query_txs_for_payload(
    user_id: int, conditions: list[dict], logic: str, scope: str, session,
) -> list[Transaction]:
    cond_objs = [
        _LegacyCondition(c["match_field"], c.get("match_op", "equals"), c["match_value"])
        for c in conditions
    ]
    cond_filter = _build_conditions_filter(cond_objs, logic, user_id, session)
    if cond_filter is None:
        return []
    return (
        session.query(Transaction)
        .filter(
            Transaction.user_id == user_id,
            cond_filter,
            *_build_scope_filter(scope),
        )
        .all()
    )


def _recompute_overrides_for_txs(user_id: int, txs: list[Transaction], session) -> int:
    if not txs:
        return 0
    all_rules = (
        session.query(TransactionRule)
        .filter_by(user_id=user_id)
        .order_by(TransactionRule.id)
        .all()
    )
    institutions = _item_institutions(user_id, session)
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
        if existing is not None and existing.source == "manual":
            continue
        matching_for_tx = [r for r in all_rules if _tx_matches_rule(tx, r, institutions)]
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
    txs = _query_txs_for_rule(rule, session)
    return _recompute_overrides_for_txs(rule.user_id, txs, session)


def snapshot_rule_txs(rule: TransactionRule, session) -> list[Transaction]:
    """Capture the txs the rule currently matches, for use before an edit."""
    return _query_txs_for_rule(rule, session)


def reapply_after_edit(
    rule: TransactionRule, old_txs: list[Transaction], session,
) -> int:
    """Recompute overrides for tx that the rule USED to match plus tx it now matches."""
    new_txs = _query_txs_for_rule(rule, session)
    by_id: dict[int, Transaction] = {t.id: t for t in new_txs}
    for t in old_txs:
        by_id.setdefault(t.id, t)
    return _recompute_overrides_for_txs(rule.user_id, list(by_id.values()), session)


def apply_rules_to_new_transactions(
    user_id: int, new_txs: list[Transaction], session,
) -> int:
    if not new_txs:
        return 0
    rules = (
        session.query(TransactionRule)
        .filter_by(user_id=user_id)
        .order_by(TransactionRule.id)
        .all()
    )
    if not rules:
        return 0
    institutions = _item_institutions(user_id, session)

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
        matched = [r for r in rules if _tx_matches_rule(tx, r, institutions)]
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


def _mirror_legacy_fields(rule: TransactionRule, conditions: list[dict]) -> None:
    """Mirror the first condition into the legacy match_* columns.

    Conditions are authoritative; legacy fields are a back-compat read path for
    single-condition rules and a placeholder for multi-condition ones.
    """
    if conditions:
        first = conditions[0]
        rule.match_field = first["match_field"]
        rule.match_op = first.get("match_op", "equals")
        rule.match_value = first["match_value"]
    else:
        rule.match_field = None
        rule.match_op = None
        rule.match_value = None


def _canonical_payload_conditions(conditions: list[dict]) -> tuple:
    """Stable key for a list of payload conditions (case-insensitive on value)."""
    return tuple(sorted(
        (
            c["match_field"],
            c.get("match_op", "equals"),
            (c.get("match_value") or "").lower(),
        )
        for c in conditions
    ))


def _canonical_rule_conditions(rule: TransactionRule) -> tuple:
    return tuple(sorted(
        (
            c.match_field,
            c.match_op or "equals",
            (c.match_value or "").lower(),
        )
        for c in rule_conditions(rule)
    ))


def find_equivalent_rule(
    user_id: int, conditions: list[dict], conditions_logic: str,
    action: str, action_value: str | None, scope: str, session,
) -> TransactionRule | None:
    """Return an existing rule with the same logical identity, or None."""
    target = _canonical_payload_conditions(conditions)
    candidates = (
        session.query(TransactionRule)
        .filter(
            TransactionRule.user_id == user_id,
            TransactionRule.action == action,
            TransactionRule.scope == scope,
            TransactionRule.conditions_logic == conditions_logic,
        )
        .all()
    )
    for r in candidates:
        if (r.action_value or None) != (action_value or None):
            continue
        if _canonical_rule_conditions(r) == target:
            return r
    return None


def create_rule(
    user_id: int, conditions: list[dict], conditions_logic: str,
    action: str, action_value: str | None, scope: str, session,
) -> TransactionRule:
    """Idempotent create: returns an existing rule with the same logical identity
    if one exists, otherwise inserts a new one.

    Identity = (user, sorted conditions, conditions_logic, action, action_value, scope).
    This prevents duplicates when a client retries after a slow apply / aborted
    request — the retry returns the same rule instead of inserting a copy.
    """
    existing = find_equivalent_rule(
        user_id, conditions, conditions_logic, action, action_value, scope, session,
    )
    if existing is not None:
        return existing

    rule = TransactionRule(
        user_id=user_id, action=action, action_value=action_value,
        scope=scope, conditions_logic=conditions_logic,
    )
    for c in conditions:
        rule.conditions.append(TransactionRuleCondition(
            match_field=c["match_field"],
            match_op=c.get("match_op", "equals"),
            match_value=c["match_value"],
        ))
    _mirror_legacy_fields(rule, conditions)
    session.add(rule)
    session.flush()
    return rule


def update_rule(
    rule: TransactionRule, conditions: list[dict], conditions_logic: str,
    action: str, action_value: str | None, scope: str, session,
) -> None:
    rule.action = action
    rule.action_value = action_value
    rule.scope = scope
    rule.conditions_logic = conditions_logic
    rule.conditions.clear()
    for c in conditions:
        rule.conditions.append(TransactionRuleCondition(
            match_field=c["match_field"],
            match_op=c.get("match_op", "equals"),
            match_value=c["match_value"],
        ))
    _mirror_legacy_fields(rule, conditions)
    session.flush()


def upsert_rule(
    user_id: int, match_field: str, match_value: str, action: str,
    action_value: str | None, session, match_op: str = "equals",
    scope: str = "all",
) -> TransactionRule:
    """Single-condition convenience used by callers and tests.

    Reuses an existing rule with the same identity tuple to keep the previous
    upsert behavior; otherwise creates a new one with a single condition row.
    """
    existing = (
        session.query(TransactionRule)
        .filter(
            TransactionRule.user_id == user_id,
            TransactionRule.action == action,
            TransactionRule.scope == scope,
        )
        .all()
    )
    target_value_lc = match_value.lower()
    match = None
    for r in existing:
        conds = rule_conditions(r)
        if (
            len(conds) == 1
            and conds[0].match_field == match_field
            and (conds[0].match_op or "equals") == match_op
            and (conds[0].match_value or "").lower() == target_value_lc
        ):
            match = r
            break

    conditions = [{
        "match_field": match_field, "match_op": match_op, "match_value": match_value,
    }]
    if match is None:
        return create_rule(
            user_id, conditions, "all", action, action_value, scope, session,
        )
    update_rule(match, conditions, "all", action, action_value, scope, session)
    return match


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
    sources = sorted({(it.institution_name or "Unknown") for it in user.items})
    return {
        "merchant": (
            [{"field": "merchant_name", "value": m, "label": m} for m in merchants]
            + [{"field": "name", "value": n, "label": n} for n in names]
        ),
        "category": [{"field": "pfc_primary", "value": p} for p in primaries],
        "item": [{"field": "pfc_detailed", "value": d} for d in detaileds],
        "source": [{"field": "source", "value": s, "label": s} for s in sources],
    }


def delete_rule(user: User, rule_id: int, session) -> bool:
    """Delete a rule and reconcile the overrides it created.

    Snapshots the txs the rule used to match, deletes the rule, then recomputes
    overrides for those txs without the rule in play. Any source='rule' overrides
    that were created only because of this rule get cleared. Manual overrides
    are protected by `_recompute_overrides_for_txs`.
    """
    row = (
        session.query(TransactionRule)
        .filter_by(id=rule_id, user_id=user.id)
        .one_or_none()
    )
    if row is None:
        return False
    old_txs = _query_txs_for_rule(row, session)
    session.delete(row)
    session.flush()
    _recompute_overrides_for_txs(user.id, old_txs, session)
    return True
