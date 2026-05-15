"""Income aggregation: pulls inflow transactions and groups by payer.

Mirrors the Spending page's shape (totals card row, stacked bar, table)
but the grouping dimension is `merchant_name` rather than `pfc_primary`,
and the sign convention is reversed — Plaid uses negative `amount` for
money flowing into the account.
"""

import hashlib
import logging
import threading
import time
from collections import defaultdict
from datetime import date, datetime

from models import Transaction, User
from spending import previous_month_window, resolve_month

log = logging.getLogger(__name__)

# Stable palette for payer dots / stacked-bar segments. Same idea as
# pfc.CATEGORY_COLORS but selected by hash since payers aren't a known set.
PAYER_PALETTE: list[str] = [
    "#22c55e", "#14b8a6", "#06b6d4", "#3b82f6", "#6366f1",
    "#8b5cf6", "#a855f7", "#ec4899", "#f97316", "#eab308",
    "#84cc16",
]


def color_for_payer(name: str) -> str:
    """Same payer always gets the same color across page loads — md5 of the
    name modulo the palette length. Not cryptographic; hash quality doesn't
    matter, just stability."""
    digest = hashlib.md5(name.encode("utf-8")).hexdigest()
    return PAYER_PALETTE[int(digest, 16) % len(PAYER_PALETTE)]


_CACHE_TTL = 60.0
_cache: dict = {}
_cache_lock = threading.Lock()
_keylocks: dict[tuple, threading.Lock] = {}


def _get_keylock(key: tuple) -> threading.Lock:
    with _cache_lock:
        lock = _keylocks.get(key)
        if lock is None:
            lock = threading.Lock()
            _keylocks[key] = lock
        return lock


def invalidate_cache(user_id: int) -> None:
    with _cache_lock:
        for k in list(_cache.keys()):
            if k[0] == user_id:
                _cache.pop(k, None)


def clear_cache() -> None:
    """Drop the entire cache. Useful for tests."""
    with _cache_lock:
        _cache.clear()
        _keylocks.clear()


def available_sources(user: User) -> list[str]:
    return sorted({(it.institution_name or "Unknown") for it in user.items})


def fetch_last_month(
    user: User, month: str | None = None, source: str | None = None, session=None,
) -> dict:
    """Aggregate this user's INCOME transactions for the chosen month.
    Returns {total, count, payers, transactions, month, month_label, source,
    daily_avg, prev_month_change_pct}.

    Plaid sign convention: positive amount = outflow (spending), negative =
    inflow (income). All amounts in the output are flipped to positive."""
    month_str, start, end, month_label = resolve_month(month)
    out: dict = {
        "total": 0.0, "count": 0, "payers": [], "transactions": [],
        "month": month_str, "month_label": month_label, "source": source,
        "daily_avg": 0.0, "prev_month_change_pct": None,
    }

    if session is None or not user.items:
        return out

    cache_key = (user.id, month_str, source or "_all")

    def _read_cached():
        with _cache_lock:
            cached = _cache.get(cache_key)
        if cached is not None:
            ts, data = cached
            if time.time() - ts < _CACHE_TTL:
                return data
        return None

    cached = _read_cached()
    if cached is not None:
        return cached

    with _get_keylock(cache_key):
        cached = _read_cached()
        if cached is not None:
            return cached
        return _fetch_uncached(
            user, source, session, start, end, month_str, month_label, cache_key,
        )


def _fetch_uncached(
    user, source, session, start, end, month_str, month_label, cache_key,
):
    out: dict = {
        "total": 0.0, "count": 0, "payers": [], "transactions": [],
        "month": month_str, "month_label": month_label, "source": source,
        "daily_avg": 0.0, "prev_month_change_pct": None,
    }

    items_by_id = {it.id: it for it in user.items}
    if source:
        items_by_id = {
            i: it for i, it in items_by_id.items()
            if (it.institution_name or "Unknown") == source
        }
        if not items_by_id:
            return out

    tx_rows = (
        session.query(Transaction)
        .filter(
            Transaction.user_id == user.id,
            Transaction.date >= start,
            Transaction.date <= end,
            Transaction.item_id.in_(list(items_by_id.keys())),
            Transaction.pfc_primary == "INCOME",
            Transaction.amount < 0,
        )
        .all()
    )

    payer_totals: dict[str, float] = defaultdict(float)
    payer_counts: dict[str, int] = defaultdict(int)
    tx_list: list[dict] = []
    for tx in tx_rows:
        payer = (tx.merchant_name or tx.name or "(unknown)").strip() or "(unknown)"
        amount = -tx.amount  # flip Plaid's inflow-is-negative to positive
        payer_totals[payer] += amount
        payer_counts[payer] += 1
        tx_list.append({
            "plaid_id": tx.plaid_transaction_id,
            "date": tx.date,
            "source": items_by_id[tx.item_id].institution_name or "Unknown",
            "payer": payer,
            "name": tx.merchant_name or tx.name or "(no description)",
            "amount": amount,
            "color": color_for_payer(payer),
        })

    out["total"] = sum(payer_totals.values())
    out["count"] = len(tx_list)
    out["payers"] = sorted(
        (
            {
                "name": p,
                "total": v,
                "count": payer_counts[p],
                "color": color_for_payer(p),
            }
            for p, v in payer_totals.items()
        ),
        key=lambda x: -x["total"],
    )
    out["transactions"] = sorted(tx_list, key=lambda t: t["date"], reverse=True)

    days_elapsed = max(1, (end - start).days + 1)
    out["daily_avg"] = out["total"] / days_elapsed

    prev_start, prev_end = previous_month_window(start, end)
    prev_total = _income_total(
        user.id, list(items_by_id.keys()), prev_start, prev_end, session,
    )
    if prev_total > 0:
        out["prev_month_change_pct"] = (out["total"] - prev_total) / prev_total * 100

    with _cache_lock:
        _cache[cache_key] = (time.time(), out)
    return out


def monthly_income_totals(user: User, session, n_months: int = 12) -> list[dict]:
    """Per-month income totals, oldest first. Mirrors spending.monthly_totals
    but selects INCOME rows with Plaid's inflow sign (amount < 0) and flips
    the totals to positive."""
    if not user.items:
        return []

    today = date.today()
    y, m = today.year, today.month
    m -= n_months - 1
    while m <= 0:
        m += 12
        y -= 1
    start = date(y, m, 1)
    end = today

    item_ids = [it.id for it in user.items]
    tx_rows = (
        session.query(Transaction)
        .filter(
            Transaction.user_id == user.id,
            Transaction.date >= start,
            Transaction.date <= end,
            Transaction.item_id.in_(item_ids),
            Transaction.pfc_primary == "INCOME",
            Transaction.amount < 0,
        )
        .all()
    )

    totals: dict[tuple[int, int], float] = defaultdict(float)
    for tx in tx_rows:
        totals[(tx.date.year, tx.date.month)] += -tx.amount

    out = []
    y, m = start.year, start.month
    for _ in range(n_months):
        out.append({
            "month": f"{y:04d}-{m:02d}",
            "label": date(y, m, 1).strftime("%b %Y"),
            "total": totals.get((y, m), 0.0),
            "ts": int(datetime(y, m, 1).timestamp()),
        })
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def _income_total(user_id, item_ids, start, end, session) -> float:
    """Total income (positive number) in the window, used for the
    vs-last-month delta. Same filter as the main query."""
    if not item_ids:
        return 0.0
    rows = (
        session.query(Transaction)
        .filter(
            Transaction.user_id == user_id,
            Transaction.date >= start,
            Transaction.date <= end,
            Transaction.item_id.in_(item_ids),
            Transaction.pfc_primary == "INCOME",
            Transaction.amount < 0,
        )
        .all()
    )
    return -sum(r.amount for r in rows)
