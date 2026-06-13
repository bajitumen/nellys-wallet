import hashlib
import logging
from collections import defaultdict
from datetime import date, datetime

from sqlalchemy import func

import rules as rules_mod
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
    digest = hashlib.md5(name.encode("utf-8")).hexdigest()
    return PAYER_PALETTE[int(digest, 16) % len(PAYER_PALETTE)]


_cache = KeyedCache(ttl_seconds=60.0)


def invalidate_cache(user_id: int) -> None:
    _cache.invalidate(user_id)


def clear_cache() -> None:
    _cache.clear()


def available_months(user: User, session, source: str | None = None) -> list[dict]:
    if not user.items:
        return []
    items_by_id = {it.id: it for it in user.items}
    if source:
        items_by_id = {
            i: it for i, it in items_by_id.items()
            if (it.institution_name or "Unknown") == source
        }
        if not items_by_id:
            return []
    # SQLite-only — swap to to_char on Postgres.
    month_col = func.strftime("%Y-%m", Transaction.date)
    rows = (
        session.query(month_col)
        .filter(
            Transaction.user_id == user.id,
            Transaction.item_id.in_(items_by_id),
            *rules_mod.build_scope_filter("income"),
            Transaction.is_internal_transfer.is_(False),
        )
        .distinct()
        .order_by(month_col.desc())
        .all()
    )
    return [
        {"value": m, "label": datetime.strptime(m, "%Y-%m").strftime("%B %Y")}
        for (m,) in rows if m
    ]


def income_amount_with_override(
    tx_amount: float, override: TransactionOverride | None,
) -> float:
    # Plaid signs inflows negative; income is shown positive. Both raw and
    # override are returned as magnitudes so a 50% split on -2500 reads +1250.
    if override and override.amount_override is not None:
        return abs(override.amount_override)
    return -tx_amount


def _apply_income_override(
    tx: Transaction, override: TransactionOverride | None,
) -> tuple[float, bool]:
    return income_amount_with_override(tx.amount, override), bool(override and override.dismissed)


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
    # Filter to amount<0 then route by RESOLVED category — matches
    # monthly_cashflow across set_category overrides.
    tx_rows = (
        session.query(Transaction)
        .filter(
            Transaction.user_id == user.id,
            Transaction.date >= prev_start,
            Transaction.date <= end,
            Transaction.item_id.in_(items_by_id),
            Transaction.amount < 0,
            Transaction.is_internal_transfer.is_(False),
        )
        .all()
    )

    overrides_by_tx = _load_overrides(
        user.id, [t.plaid_transaction_id for t in tx_rows], session,
    )
    rule_id_by_tx = rules_mod.applied_rule_id_by_tx(tx_rows, user.id, session)

    payer_totals: dict[str, float] = defaultdict(float)
    payer_counts: dict[str, int] = defaultdict(int)
    tx_list: list[dict] = []
    prev_total = 0.0
    import pfc as _pfc
    for tx in tx_rows:
        if tx.iso_currency_code and tx.iso_currency_code != "USD":
            continue
        override = overrides_by_tx.get(tx.plaid_transaction_id)
        resolved = (
            (override.category_override if override and override.category_override else None)
            or tx.pfc_primary
        )
        if not _pfc.is_strict_income(resolved):
            continue
        amount, dismissed = _apply_income_override(tx, override)
        if dismissed:
            if not (start <= tx.date <= end):
                continue
        else:
            if prev_start <= tx.date <= prev_end:
                prev_total += amount
                continue
            if not (start <= tx.date <= end):
                continue
        payer = (tx.merchant_name or tx.name or "(unknown)").strip() or "(unknown)"
        if not dismissed:
            payer_totals[payer] += amount
            payer_counts[payer] += 1
        tx_list.append({
            "plaid_id": tx.plaid_transaction_id,
            "date": tx.date,
            "source": items_by_id[tx.item_id].institution_name or "Unknown",
            "payer": payer,
            "name": tx.merchant_name or tx.name or "(no description)",
            "amount": amount,
            "original_amount": -tx.amount,
            "color": color_for_payer(payer),
            "dismissed": dismissed,
            "category_raw": tx.pfc_primary,
            "detailed_raw": tx.pfc_detailed,
            "rule_id": rule_id_by_tx.get(tx.plaid_transaction_id),
        })

    out["total"] = round(sum(payer_totals.values()), 2)
    # Count excludes dismissed; tx_list keeps them for the restore action.
    out["count"] = sum(payer_counts.values())
    out["payers"] = sorted(
        (
            {
                "name": p,
                "total": round(v, 2),
                "count": payer_counts[p],
                "color": color_for_payer(p),
            }
            for p, v in payer_totals.items()
        ),
        key=lambda x: -x["total"],
    )
    out["transactions"] = sorted(tx_list, key=lambda t: t["date"], reverse=True)

    days_elapsed = max(1, (end - start).days + 1)
    out["daily_avg"] = round(out["total"] / days_elapsed, 2)

    if prev_total > 0:
        out["prev_month_change_pct"] = round((out["total"] - prev_total) / prev_total * 100, 1)

    return out


def monthly_income_totals(user: User, session, n_months: int = 12) -> list[dict]:
    from spending import monthly_cashflow
    return [
        {"month": row["month"], "label": row["label"],
         "total": row["income"], "ts": row["ts"]}
        for row in monthly_cashflow(user, session, n_months=n_months)
    ]
