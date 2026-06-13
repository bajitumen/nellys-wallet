from sqlalchemy import and_, false, func, or_, true
from sqlalchemy.orm import selectinload

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



class _PayloadCondition:
    __slots__ = ("match_field", "match_op", "match_value")

    def __init__(self, field, op, value):
        self.match_field = field
        self.match_op = op or "equals"
        self.match_value = value


def rule_conditions(rule: TransactionRule) -> list:
    return list(rule.conditions)


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
    """Which /rules section(s) the rule appears in.

    Honors the user's explicit scope choice: 'spending' / 'income' go to that
    section only; 'all' goes to BOTH sections. We deliberately don't infer
    from match fields anymore — the user picked 'Both' for a reason.
    """
    if rule.scope == "spending":
        return "spending"
    if rule.scope == "income":
        return "income"
    return "both"


def action_label(rule: TransactionRule) -> str:
    if rule.action == "dismiss":
        return "dismiss"
    if rule.action == "set_category":
        return f"categorize as {pfc_mod.humanize_primary(rule.action_value)}"
    if rule.action == "set_detailed":
        return f"label as {pfc_mod.humanize_detailed(rule.action_value)}"
    if rule.action == "split":
        return f"split so my share is {rule.action_value}%"
    if rule.action == "split_dollar":
        return f"split so my share is ${rule.action_value}"
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


def tx_in_scope(tx: Transaction, scope: str) -> bool:
    if scope == "all":
        return True
    if scope == "spending":
        # Treat NULL pfc_primary as spend-side. Plaid sometimes returns no
        # category at all; falling through silently used to drop the row from
        # every total (Python `None not in {...}` is True, but the SQL
        # `pfc_primary NOT IN (...)` ignores NULLs and excludes the row).
        if tx.amount is None or tx.amount <= 0:
            return False
        return tx.pfc_primary is None or pfc_mod.is_spend_category(tx.pfc_primary)
    if scope == "income":
        return tx.amount is not None and tx.amount < 0 and pfc_mod.is_strict_income(tx.pfc_primary)
    return False


def build_scope_filter(scope: str):
    if scope == "spending":
        # Match tx_in_scope: NULL pfc_primary is treated as spend-side.
        return [
            Transaction.amount > 0,
            or_(
                Transaction.pfc_primary.is_(None),
                ~Transaction.pfc_primary.in_(pfc_mod.INCOME_SIDE_PRIMARIES),
            ),
        ]
    if scope == "income":
        return [Transaction.amount < 0, Transaction.pfc_primary == "INCOME"]
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
    if not tx_in_scope(tx, rule.scope):
        return False
    conds = rule_conditions(rule)
    if not conds:
        return False
    matches = (_condition_matches_tx(tx, c, item_institutions) for c in conds)
    return any(matches) if rule.conditions_logic == "any" else all(matches)


def _rank_key(rule: TransactionRule, specificity: dict[int, int]) -> tuple:
    # Sorts ascending: most specific first, older id (lower) breaks ties.
    return (-specificity[rule.id], rule.id if rule.id is not None else 0)


# Dependency-order for applying winners. set_category resets detailed (in
# _apply_rule_to_override), so a winning set_detailed must apply AFTER any
# winning set_category — otherwise the detailed rule's effect is silently
# clobbered and the outcome flips based on rule creation order.
_ACTION_APPLY_ORDER = ("dismiss", "set_category", "set_detailed", "split")


def _winning_rules(
    matched: list[TransactionRule], specificity: dict[int, int],
) -> list[TransactionRule]:
    """Per action group, keep only the most specific matching rule.

    Tie-break is deterministic: on equal specificity the older rule wins
    (lower id). Without this, the result depends on row order from the DB
    and the same tx can flap between rules across syncs.

    Returned order is the dependency-order needed by _apply_rule_to_override.
    """
    by_group: dict[str, TransactionRule] = {}
    for r in sorted(matched, key=lambda r: _rank_key(r, specificity)):
        key = _action_group(r.action)
        if key not in by_group:
            by_group[key] = r

    def order(group: str) -> int:
        try:
            return _ACTION_APPLY_ORDER.index(group)
        except ValueError:
            return len(_ACTION_APPLY_ORDER)
    return [by_group[g] for g in sorted(by_group.keys(), key=order)]


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
        # A detailed code from a different primary than the tx's resolved
        # category would land in a (primary, detailed) cell _subitems_for
        # never iterates, leaving the amount in the primary total but in
        # no subitem. Match the override-endpoint guard.
        target_primary = ov.category_override or (tx.pfc_primary if tx is not None else None)
        if (
            rule.action_value
            and target_primary
            and pfc_mod.primary_of(rule.action_value) != target_primary
        ):
            return
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


def query_txs(user_id: int, conditions, logic: str, scope: str, session) -> list[Transaction]:
    cond_filter = _build_conditions_filter(conditions, logic, user_id, session)
    if cond_filter is None:
        return []
    return (
        session.query(Transaction)
        .filter(
            Transaction.user_id == user_id,
            cond_filter,
            *build_scope_filter(scope),
        )
        .all()
    )


def _query_txs_for_rule(rule: TransactionRule, session) -> list[Transaction]:
    return query_txs(rule.user_id, rule_conditions(rule), rule.conditions_logic, rule.scope, session)


def query_txs_for_payload(
    user_id: int, conditions: list[dict], logic: str, scope: str, session,
) -> list[Transaction]:
    cond_objs = [
        _PayloadCondition(c["match_field"], c.get("match_op", "equals"), c["match_value"])
        for c in conditions
    ]
    return query_txs(user_id, cond_objs, logic, scope, session)


def _recompute_overrides_for_txs(user_id: int, txs: list[Transaction], session) -> int:
    if not txs:
        return 0
    all_rules = (
        session.query(TransactionRule).options(selectinload(TransactionRule.conditions))
        .filter_by(user_id=user_id)
        .order_by(TransactionRule.id)
        .all()
    )
    institutions = _item_institutions(user_id, session)
    specificity = {r.id: rule_specificity(r) for r in all_rules}
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
        winners = _winning_rules(matching_for_tx, specificity)
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


def applied_rule_id_by_tx(
    txs: list[Transaction], user_id: int, session,
) -> dict[str, int]:
    """Return {plaid_transaction_id: rule_id} for the rule shown on each tx row.

    Picks the most-specific of the rules that actually set the tx's override
    (the same winners `_winning_rules` applies), so the "Edit rule" affordance
    always points at a rule affecting the row.
    """
    if not txs:
        return {}
    rules = (
        session.query(TransactionRule).options(selectinload(TransactionRule.conditions))
        .filter_by(user_id=user_id)
        .order_by(TransactionRule.id)
        .all()
    )
    if not rules:
        return {}
    institutions = _item_institutions(user_id, session)
    specificity = {r.id: rule_specificity(r) for r in rules}
    out: dict[str, int] = {}
    for tx in txs:
        matched = [r for r in rules if _tx_matches_rule(tx, r, institutions)]
        winners = _winning_rules(matched, specificity)
        if not winners:
            continue
        out[tx.plaid_transaction_id] = min(
            winners, key=lambda r: _rank_key(r, specificity),
        ).id
    return out


def rules_by_id_dict(user_id: int, session, rule_ids: list[int]) -> dict[str, dict]:
    """Serialize the named rules into the shape the rule-modal expects."""
    if not rule_ids:
        return {}
    rows = (
        session.query(TransactionRule).options(selectinload(TransactionRule.conditions))
        .filter(
            TransactionRule.user_id == user_id,
            TransactionRule.id.in_(rule_ids),
        )
        .all()
    )
    return {
        str(r.id): {
            "id": r.id,
            "conditions": [
                {
                    "match_field": c.match_field,
                    "match_op": c.match_op,
                    "match_value": c.match_value,
                }
                for c in rule_conditions(r)
            ],
            "conditions_logic": r.conditions_logic,
            "action": r.action,
            "action_value": r.action_value,
            "scope": r.scope,
        }
        for r in rows
    }


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
        session.query(TransactionRule).options(selectinload(TransactionRule.conditions))
        .filter_by(user_id=user_id)
        .order_by(TransactionRule.id)
        .all()
    )
    if not rules:
        return 0
    institutions = _item_institutions(user_id, session)
    specificity = {r.id: rule_specificity(r) for r in rules}

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
        winners = _winning_rules(matched, specificity)
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
    match_action_value: bool = True,
) -> TransactionRule | None:
    """Return an existing rule with the same logical identity, or None.

    With match_action_value=False the action_value is excluded from identity,
    so the match is on trigger alone (used by upsert to update value in place).
    """
    target = _canonical_payload_conditions(conditions)
    candidates = (
        session.query(TransactionRule).options(selectinload(TransactionRule.conditions))
        .filter(
            TransactionRule.user_id == user_id,
            TransactionRule.action == action,
            TransactionRule.scope == scope,
            TransactionRule.conditions_logic == conditions_logic,
        )
        .all()
    )
    for r in candidates:
        if match_action_value and (r.action_value or None) != (action_value or None):
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
    session.flush()


def upsert_rule(
    user_id: int, match_field: str, match_value: str, action: str,
    action_value: str | None, session, match_op: str = "equals",
    scope: str = "all",
) -> TransactionRule:
    """Single-condition convenience used by callers and tests.

    Upsert by trigger: a rule with the same condition/action/scope has its
    action_value updated in place; otherwise a new rule is created.
    """
    conditions = [{
        "match_field": match_field, "match_op": match_op, "match_value": match_value,
    }]
    existing = find_equivalent_rule(
        user_id, conditions, "all", action, action_value, scope, session,
        match_action_value=False,
    )
    if existing is None:
        return create_rule(
            user_id, conditions, "all", action, action_value, scope, session,
        )
    update_rule(existing, conditions, "all", action, action_value, scope, session)
    return existing


def list_rules(user: User, session) -> list[TransactionRule]:
    return (
        session.query(TransactionRule).options(selectinload(TransactionRule.conditions))
        .filter_by(user_id=user.id)
        .order_by(TransactionRule.created_at.desc())
        .all()
    )


_match_options_cache = None  # set below after the function is defined


def user_match_options(user: User, session) -> dict:
    """Cached: this runs 4 DISTINCT scans + a sort over the whole user's
    transaction set. The spending/income pages call it on every load, every
    refocus, every chip click. The cache is invalidated on sync alongside
    spending/income (see invalidate_cache below)."""
    global _match_options_cache
    if _match_options_cache is None:
        from cache import KeyedCache
        _match_options_cache = KeyedCache(ttl_seconds=60.0)
    return _match_options_cache.get_or_compute(
        (user.id,), lambda: _user_match_options_uncached(user, session)
    )


def invalidate_cache(user_id: int) -> None:
    global _match_options_cache
    if _match_options_cache is not None:
        _match_options_cache.invalidate(user_id)


def _user_match_options_uncached(user: User, session) -> dict:
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
        session.query(TransactionRule).options(selectinload(TransactionRule.conditions))
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
