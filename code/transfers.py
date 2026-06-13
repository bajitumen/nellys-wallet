from collections import defaultdict
from datetime import timedelta

from models import Transaction


DEFAULT_WINDOW_DAYS = 3

# Account-to-account PFC codes only — restricting to these stops a Zelle-out
# from falsely pairing with a same-amount external deposit.
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
    # Reset every flagged row, not just current TRANSFER_* candidates — Plaid
    # can recategorize a paired leg (TRANSFER_IN → INCOME), and resetting
    # only candidates leaves the recategorized leg stuck hidden forever.
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
        # Sessions are autoflush=False; flush so later filters see the writes.
        session.flush()
    return paired
