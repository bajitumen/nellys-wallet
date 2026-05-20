import hashlib
import logging
from collections import defaultdict
from datetime import date, datetime

from cache import KeyedCache
from models import Transaction, TransactionOverride, User
from spending import _load_overrides, previous_month_window, resolve_month

log = logging.getLogger(__name__)

PAYER_PALETTE: list[str] = [
    "#22c55e", "#14b8a6", "#06b6d4", "#3b82f6", "#6366f1",
    "#8b5cf6", "#a855f7", "#ec4899", "#f97316", "#eab308",
    "#84cc16",
]


def color_for_payer(name: str) -> str:
    # md5 only for stable mapping; not cryptographic.
    digest = hashlib.md5(name.encode("utf-8")).hexdigest()
    return PAYER_PALETTE[int(digest, 16) % len(PAYER_PALETTE)]


_cache = KeyedCache(ttl_seconds=0.0)


def invalidate_cache(user_id: int) -> None:
    _cache.invalidate(user_id)


def clear_cache() -> None:
    _cache.clear()


def available_sources(user: User) -> list[str]:
    return sorted({(it.institution_name or "Unknown") for it in user.items})


def _apply_income_override(tx: Transaction, override: TransactionOverride | None) -> float | None:
    if override and override.dismissed:
        return None
    amount = -tx.amount  # Plaid: negative=inflow, so flip sign for income.
    if override and override.amount_override is not None:
        amount = override.amount_override
    return amount


def fetch_last_month(
    user: User, month: str | None = None, source: str | None = None, session=None,
) -> dict:
    month_str, start, end, month_label = resolve_month(month)
    empty: dict = {
        "total": 0.0, "count": 0, "payers": [], "transactions": [],
        "month": month_str, "month_label": month_label, "source": source,
        "daily_avg": 0.0, "prev_month_change_pct": None,
    }

    if session is None or not user.items:
        return empty

    cache_key = (user.id, month_str, source or "_all")
    return _cache.get_or_compute(
        cache_key,
        lambda: _fetch_uncached(
            user, source, session, start, end, month_str, month_label,
        ),
    )


def _fetch_uncached(user, source, session, start, end, month_str, month_label):
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

    prev_start, prev_end = previous_month_window(start, end)
    tx_rows = (
        session.query(Transaction)
        .filter(
            Transaction.user_id == user.id,
            Transaction.date >= prev_start,
            Transaction.date <= end,
            Transaction.item_id.in_(list(items_by_id.keys())),
            Transaction.pfc_primary == "INCOME",
            Transaction.amount < 0,
        )
        .all()
    )

    overrides_by_tx = _load_overrides(
        user.id, [t.plaid_transaction_id for t in tx_rows], session,
    )

    payer_totals: dict[str, float] = defaultdict(float)
    payer_counts: dict[str, int] = defaultdict(int)
    tx_list: list[dict] = []
    prev_total = 0.0
    for tx in tx_rows:
        amount = _apply_income_override(tx, overrides_by_tx.get(tx.plaid_transaction_id))
        if amount is None:
            continue
        if prev_start <= tx.date <= prev_end:
            prev_total += amount
            continue
        if not (start <= tx.date <= end):
            continue
        payer = (tx.merchant_name or tx.name or "(unknown)").strip() or "(unknown)"
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

    if prev_total > 0:
        out["prev_month_change_pct"] = (out["total"] - prev_total) / prev_total * 100

    return out


def monthly_income_totals(user: User, session, n_months: int = 12) -> list[dict]:
    from spending import monthly_cashflow
    return [
        {"month": row["month"], "label": row["label"],
         "total": row["income"], "ts": row["ts"]}
        for row in monthly_cashflow(user, session, n_months=n_months)
    ]
