"""Pair-matching for internal transfers between a user's own accounts.

A "pair" = one TRANSFER_OUT and one TRANSFER_IN belonging to the same user with
matching absolute amount and dates within a small window. Both legs get
`Transaction.is_internal_transfer = True`. The spending/income filters then
drop only flagged rows when the user opts out of counting internal transfers,
leaving genuine outflows (Zelle to a friend) and inflows (deposit from work)
alone — those have no opposite leg on the user's own accounts to pair with.
"""

from collections import defaultdict
from datetime import timedelta

from models import Transaction


DEFAULT_WINDOW_DAYS = 3


def _amt_key(tx: Transaction) -> float:
    return round(abs(tx.amount or 0), 2)


def pair_internal_transfers(
    user_id: int, session, window_days: int = DEFAULT_WINDOW_DAYS,
) -> int:
    """Flag unpaired TRANSFER_OUT/TRANSFER_IN pairs as internal transfers.

    Greedy match by (|amount|, date proximity). Each tx is matched at most once.
    Returns the number of newly-flagged transactions (so a fully successful run
    on N pairs returns 2N).
    """
    unpaired = (
        session.query(Transaction)
        .filter(
            Transaction.user_id == user_id,
            Transaction.is_internal_transfer.is_(False),
            Transaction.pfc_primary.in_(("TRANSFER_OUT", "TRANSFER_IN")),
        )
        .order_by(Transaction.date.asc(), Transaction.id.asc())
        .all()
    )
    if not unpaired:
        return 0

    ins_by_amt: dict[float, list[Transaction]] = defaultdict(list)
    outs: list[Transaction] = []
    for t in unpaired:
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
