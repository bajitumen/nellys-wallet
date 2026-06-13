"""Pair-matching for internal transfers between a user's own accounts.

A "pair" = one TRANSFER_OUT and one TRANSFER_IN belonging to the same user with
matching absolute amount and dates within a small window. Both legs get
`Transaction.is_internal_transfer = True`, which the spending/income filters
drop, leaving genuine outflows (Zelle to a friend) and inflows (deposit from
work) alone — those have no opposite leg on the user's own accounts to pair with.
"""

from collections import defaultdict
from datetime import timedelta

from models import Transaction


DEFAULT_WINDOW_DAYS = 3

# Plaid PFC detailed codes that mean "between my own accounts" rather than
# "in/out from a third party". Restricting pairing to these prevents false
# positives like a real Zelle-out coincidentally matching a $X external deposit.
INTERNAL_OUT_DETAILED = frozenset({
    "TRANSFER_OUT_ACCOUNT_TRANSFER",
    "TRANSFER_OUT_SAVINGS",
    "TRANSFER_OUT_INVESTMENT_AND_RETIREMENT_FUNDS",
})
INTERNAL_IN_DETAILED = frozenset({
    "TRANSFER_IN_ACCOUNT_TRANSFER",
    "TRANSFER_IN_SAVINGS",
    "TRANSFER_IN_INVESTMENT_AND_RETIREMENT_FUNDS",
})


def _amt_key(tx: Transaction) -> float:
    return round(abs(tx.amount or 0), 2)


def pair_internal_transfers(
    user_id: int, session, window_days: int = DEFAULT_WINDOW_DAYS,
) -> int:
    """Flag TRANSFER_OUT/TRANSFER_IN pairs as internal transfers.

    Greedy match by (|amount|, date proximity). The two legs of a real same-
    bank transfer (BoA checking → BoA savings) share one PlaidItem, so we do
    NOT require different items; instead we restrict pairing to Plaid's
    explicit *_ACCOUNT_TRANSFER / _SAVINGS / _INVESTMENT detailed codes — a
    generic Zelle-out won't accidentally pair with a same-amount deposit.

    Each tx is matched at most once. Returns the number of newly-flagged
    transactions (so a fully successful run on N pairs returns 2N).

    Idempotency: clears stale is_internal_transfer flags across the user's
    entire transaction set before re-pairing — not just the current
    TRANSFER_* candidate set — so a Plaid recategorization (e.g. TRANSFER_IN
    → INCOME) doesn't leave a phantom pair hidden forever.
    """
    # Reset is_internal_transfer for EVERY currently-flagged row this user
    # owns, not just rows that still classify as TRANSFER_*. Plaid sometimes
    # recategorizes a previously-paired leg's pfc_primary (e.g. a TRANSFER_IN
    # becomes INCOME after the bank tags it as payroll). If we only reset
    # within the TRANSFER_* candidate set, the recategorized leg keeps a
    # stale True flag and disappears from every total forever.
    (
        session.query(Transaction)
        .filter(
            Transaction.user_id == user_id,
            Transaction.is_internal_transfer.is_(True),
        )
        .update({"is_internal_transfer": False}, synchronize_session="fetch")
    )
    session.flush()

    rows = (
        session.query(Transaction)
        .filter(
            Transaction.user_id == user_id,
            Transaction.pfc_primary.in_(("TRANSFER_OUT", "TRANSFER_IN")),
        )
        .order_by(Transaction.date.asc(), Transaction.id.asc())
        .all()
    )
    if not rows:
        return 0

    def is_internal(t: Transaction) -> bool:
        if t.pfc_primary == "TRANSFER_OUT":
            return t.pfc_detailed in INTERNAL_OUT_DETAILED
        if t.pfc_primary == "TRANSFER_IN":
            return t.pfc_detailed in INTERNAL_IN_DETAILED
        return False

    ins_by_amt: dict[float, list[Transaction]] = defaultdict(list)
    outs: list[Transaction] = []
    for t in rows:
        # Only pair things Plaid explicitly tagged as account-to-account — a
        # generic TRANSFER_IN can be a friend's Zelle (real income); a generic
        # TRANSFER_OUT can be a Zelle to a friend (real spending). Pairing
        # those by amount alone produces false positives.
        if not is_internal(t):
            continue
        if t.pfc_primary == "TRANSFER_IN":
            ins_by_amt[_amt_key(t)].append(t)
        else:
            outs.append(t)

    paired = 0
    used_in_ids: set[int] = set()
    window = timedelta(days=window_days)
    for o in outs:
        candidates = ins_by_amt.get(_amt_key(o), [])
        best: Transaction | None = None
        best_gap: timedelta | None = None
        for c in candidates:
            if c.id in used_in_ids:
                continue
            gap = abs(c.date - o.date)
            if gap > window:
                continue
            if best is None or gap < best_gap:
                best = c
                best_gap = gap
        if best is None:
            continue
        o.is_internal_transfer = True
        best.is_internal_transfer = True
        used_in_ids.add(best.id)
        paired += 2
    if paired:
        # Force flush so a subsequent call sees the updated flags via the
        # `is_(False)` SQL filter (sessions in this app are autoflush=False).
        session.flush()
    return paired
