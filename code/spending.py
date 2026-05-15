"""Spending: locally persisted transactions, per-month aggregation, overrides.

`sync_transactions(user, session)` pulls a date range from Plaid and upserts
into the local `transactions` table — the **only** function that hits Plaid.

`fetch_last_month(user, ...)` reads from the local table, applies user
overrides, aggregates by category. Wrapped in a short-lived in-memory cache so
repeated page loads don't repeat the DB scan.
"""

import logging
import threading
import time
from calendar import monthrange
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import plaid
from plaid.model.transactions_get_request import TransactionsGetRequest
from plaid.model.transactions_get_request_options import TransactionsGetRequestOptions

import budget as budget_mod
import pfc
from models import PlaidItem, Transaction, TransactionOverride, User
from providers import plaid_client_for

log = logging.getLogger(__name__)

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


def available_sources(user: User) -> list[str]:
    """Institution names of linked items, deduped and sorted."""
    return sorted({(item.institution_name or "Unknown") for item in user.items})


def resolve_month(month: str | None) -> tuple[str, date, date, str]:
    """Parse 'YYYY-MM' into (month_str, start, end, label). Caps end at today
    if the month extends into the future. Falls back to the current month if
    `month` is empty or malformed."""
    today = date.today()
    try:
        y_str, m_str = month.split("-")
        y, m = int(y_str), int(m_str)
        if not (1 <= m <= 12):
            raise ValueError
        start = date(y, m, 1)
        end = date(y, m, monthrange(y, m)[1])
    except (ValueError, AttributeError):
        y, m = today.year, today.month
        start = date(y, m, 1)
        end = date(y, m, monthrange(y, m)[1])
    if end > today:
        end = today
    return f"{y:04d}-{m:02d}", start, end, start.strftime("%B %Y")


def invalidate_cache(user_id: int) -> None:
    """Drop all cached fetch_last_month results for a user. Call after sync
    or an override change."""
    with _cache_lock:
        for k in list(_cache.keys()):
            if k[0] == user_id:
                _cache.pop(k, None)


def clear_cache() -> None:
    """Drop the entire cache. Useful for tests."""
    with _cache_lock:
        _cache.clear()
        _keylocks.clear()


# ---------------------------------------------------------------------------
# Plaid → DB sync
# ---------------------------------------------------------------------------

def _fetch_raw_transactions(client, item, start: date, end: date) -> dict:
    """Pull all transactions for one item across the date range. Paginates."""
    out: dict = {"transactions": [], "errors": []}
    institution = item.institution_name or "Unknown"
    token = item.get_access_token()
    page_size = 250
    offset = 0

    while True:
        try:
            resp = client.transactions_get(TransactionsGetRequest(
                access_token=token,
                start_date=start,
                end_date=end,
                options=TransactionsGetRequestOptions(count=page_size, offset=offset),
            ))
        except plaid.ApiException as e:
            body = getattr(e, "body", str(e)) or ""
            log.warning("transactions_get failed for %s: %s", institution, body[:500])
            if "PRODUCT_NOT_READY" in body:
                out["errors"].append(f"{institution}: transactions not yet ready")
            elif "NO_ACCOUNTS" in body or "PRODUCTS_NOT_SUPPORTED" in body:
                pass  # quiet skip
            else:
                out["errors"].append(f"{institution} transactions: {body[:200]}")
            break

        out["transactions"].extend(resp.transactions)
        if len(resp.transactions) < page_size:
            break
        offset += page_size

    return out


def sync_transactions(user: User, session, days: int = 90) -> dict:
    """Pull the last `days` days of Plaid transactions and upsert into the
    local `transactions` table. Idempotent; safe to re-run.
    Returns {added, updated, errors}."""
    out: dict = {"added": 0, "updated": 0, "errors": []}

    if not user.items:
        return out

    try:
        client = plaid_client_for(user)
    except ValueError as e:
        out["errors"].append(str(e))
        return out

    end = date.today()
    start = end - timedelta(days=days)

    items = list(user.items)
    with ThreadPoolExecutor(max_workers=min(8, len(items))) as ex:
        per_item = list(ex.map(
            lambda it: (it, _fetch_raw_transactions(client, it, start, end)),
            items,
        ))

    existing = {
        t.plaid_transaction_id: t
        for t in session.query(Transaction)
        .filter(
            Transaction.user_id == user.id,
            Transaction.date >= start,
            Transaction.date <= end,
        )
        .all()
    }

    for item, result in per_item:
        out["errors"].extend(result["errors"])
        for tx in result["transactions"]:
            pfc_obj = getattr(tx, "personal_finance_category", None)
            pfc_primary = pfc_obj.primary if pfc_obj and getattr(pfc_obj, "primary", None) else None
            pfc_detailed = (
                pfc_obj.detailed if pfc_obj and getattr(pfc_obj, "detailed", None) else None
            )
            row = existing.get(tx.transaction_id)
            if row is not None:
                row.amount = float(tx.amount or 0)
                row.name = tx.name
                row.merchant_name = getattr(tx, "merchant_name", None)
                row.pfc_primary = pfc_primary
                row.pfc_detailed = pfc_detailed
                row.item_id = item.id
                out["updated"] += 1
            else:
                session.add(Transaction(
                    user_id=user.id,
                    item_id=item.id,
                    plaid_transaction_id=tx.transaction_id,
                    date=tx.date,
                    amount=float(tx.amount or 0),
                    name=tx.name,
                    merchant_name=getattr(tx, "merchant_name", None),
                    pfc_primary=pfc_primary,
                    pfc_detailed=pfc_detailed,
                ))
                out["added"] += 1

    user.last_transactions_sync = datetime.now(timezone.utc).replace(tzinfo=None)
    session.commit()
    invalidate_cache(user.id)
    # New transactions can land in either spend or income buckets; bust both.
    import income as _income
    _income.invalidate_cache(user.id)
    log.info(
        "sync_transactions user_id=%s added=%s updated=%s errors=%s",
        user.id, out["added"], out["updated"], len(out["errors"]),
    )
    return out


# ---------------------------------------------------------------------------
# DB → page
# ---------------------------------------------------------------------------

def fetch_last_month(
    user: User, month: str | None = None, source: str | None = None, session=None,
) -> dict:
    """Read transactions from the local DB for a month, apply overrides, and
    aggregate. Returns the same shape as before:
    {total, count, categories, errors, source, transactions, month, month_label}."""
    month_str, start, end, month_label = resolve_month(month)
    out: dict = {
        "total": 0.0, "count": 0, "categories": [], "errors": [],
        "source": source, "transactions": [],
        "month": month_str, "month_label": month_label,
        "daily_avg": 0.0,
        "prev_month_change_pct": None,
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

    # Serialize concurrent misses for the same (user, month, source).
    with _get_keylock(cache_key):
        cached = _read_cached()
        if cached is not None:
            return cached

        return _fetch_last_month_uncached(
            user, source, session, start, end, month_str, month_label, cache_key,
        )


def _fetch_last_month_uncached(
    user, source, session, start, end, month_str, month_label, cache_key,
):
    """The pre-cache work for fetch_last_month, factored out so both the
    fast-path and the locked path can call the same code."""
    out: dict = {
        "total": 0.0, "count": 0, "categories": [], "errors": [],
        "source": source, "transactions": [],
        "month": month_str, "month_label": month_label,
        "daily_avg": 0.0,
        "prev_month_change_pct": None,
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
            Transaction.amount > 0,
        )
        .all()
    )

    # Only load overrides for the txs in scope, not every override the user
    # has ever made. Avoids reading rows that contribute nothing to this page.
    tx_ids = [t.plaid_transaction_id for t in tx_rows]
    overrides_by_tx: dict[str, TransactionOverride] = (
        {
            o.plaid_transaction_id: o
            for o in session.query(TransactionOverride)
            .filter(
                TransactionOverride.user_id == user.id,
                TransactionOverride.plaid_transaction_id.in_(tx_ids),
            )
            .all()
        }
        if tx_ids else {}
    )

    totals: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    tx_list: list[dict] = []
    for tx in tx_rows:
        ov = overrides_by_tx.get(tx.plaid_transaction_id)
        if ov and ov.dismissed:
            continue  # user dismissed this tx — exclude from list and totals

        category = tx.pfc_primary or "UNKNOWN"
        amount = tx.amount
        split_percentage = None
        detailed = None
        if ov:
            if ov.category_override:
                category = ov.category_override
            if ov.amount_override is not None:
                amount = ov.amount_override
            split_percentage = ov.split_percentage
            detailed = ov.detailed_override
        # Fall back to Plaid's detailed only when no override exists AND the
        # detailed belongs to the displayed category (a stale Plaid detail
        # under a user-recategorized row would be misleading).
        if detailed is None and tx.pfc_detailed and pfc.primary_of(tx.pfc_detailed) == category:
            detailed = tx.pfc_detailed

        if category in pfc.EXCLUDED_CATEGORIES:
            continue
        totals[category] += amount
        counts[category] += 1
        tx_list.append({
            "plaid_id": tx.plaid_transaction_id,
            "date": tx.date,
            "source": items_by_id[tx.item_id].institution_name or "Unknown",
            "name": tx.merchant_name or tx.name or "(no description)",
            "category": pfc.humanize_primary(category),
            "category_raw": category,
            "detailed_raw": detailed,
            "detailed_label": (
                pfc.humanize_detailed(detailed, category) if detailed else None
            ),
            "amount": amount,
            "split_percentage": split_percentage,
        })

    out["total"] = sum(totals.values())
    out["count"] = len(tx_list)

    # Per-primary budgets = sum of each primary's detailed budget rows.
    # One query, summed in Python — cheaper than 13 separate primary_sum calls.
    primary_budgets: dict[str, float] = defaultdict(float)
    for detailed, amount in budget_mod.get_budgets(user, session).items():
        primary = pfc.primary_of(detailed)
        if primary:
            primary_budgets[primary] += amount

    # Without a source filter ("All" tab), show every spending primary even
    # when there was no spend in that bucket — gives the budget column
    # something to display for un-spent categories.
    category_keys = set(totals.keys())
    if source is None:
        category_keys.update(pfc.PFC_TAXONOMY.keys())

    out["categories"] = sorted(
        (
            {
                "name": pfc.humanize_primary(k),
                "total": totals.get(k, 0.0),
                "count": counts.get(k, 0),
                "color": pfc.CATEGORY_COLORS.get(k, pfc.DEFAULT_COLOR),
                "budget": primary_budgets.get(k, 0.0),
            }
            for k in category_keys
        ),
        key=lambda c: -c["total"],
    )
    out["transactions"] = sorted(tx_list, key=lambda t: t["date"], reverse=True)

    days_elapsed = max(1, (end - start).days + 1)
    out["daily_avg"] = out["total"] / days_elapsed

    prev_start, prev_end = previous_month_window(start, end)
    prev_total = _spending_total(
        user.id, list(items_by_id.keys()), prev_start, prev_end,
        overrides_by_tx, session,
    )
    if prev_total > 0:
        out["prev_month_change_pct"] = (out["total"] - prev_total) / prev_total * 100

    with _cache_lock:
        _cache[cache_key] = (time.time(), out)
    return out


def previous_month_window(start: date, end: date) -> tuple[date, date]:
    """Day-aligned previous-month window for vs.-last-month comparisons.
    May 1–12 → April 1–12; March 1–31 → February 1–28 (capped at month length)."""
    if start.month == 1:
        prev_start = date(start.year - 1, 12, 1)
    else:
        prev_start = date(start.year, start.month - 1, 1)
    days = (end - start).days + 1
    prev_month_len = monthrange(prev_start.year, prev_start.month)[1]
    prev_end = date(prev_start.year, prev_start.month, min(days, prev_month_len))
    return prev_start, prev_end


def _spending_total(
    user_id: int, item_ids: list[int], start: date, end: date,
    overrides_by_tx: dict, session,
) -> float:
    """Sum positive, non-excluded transaction amounts in the window with
    overrides applied. Used by fetch_last_month for the prev-month comparison."""
    if not item_ids:
        return 0.0
    rows = (
        session.query(Transaction)
        .filter(
            Transaction.user_id == user_id,
            Transaction.date >= start,
            Transaction.date <= end,
            Transaction.item_id.in_(item_ids),
            Transaction.amount > 0,
        )
        .all()
    )
    total = 0.0
    for tx in rows:
        ov = overrides_by_tx.get(tx.plaid_transaction_id)
        if ov and ov.dismissed:
            continue
        category = tx.pfc_primary or "UNKNOWN"
        amount = tx.amount
        if ov:
            if ov.category_override:
                category = ov.category_override
            if ov.amount_override is not None:
                amount = ov.amount_override
        if category in pfc.EXCLUDED_CATEGORIES:
            continue
        total += amount
    return total


def monthly_totals(user: User, session, n_months: int = 6) -> list[dict]:
    """Total spending per month over the last `n_months`, oldest first.
    Months with no recorded transactions return 0 — keeps the chart's x-axis
    consistent even when the 90-day sync window doesn't reach back far enough.

    Each entry: {month: 'YYYY-MM', label: 'May 2026', total: 1234.56}.
    Aggregation respects user overrides and EXCLUDED_CATEGORIES, same as the
    per-month Spending page."""
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
            Transaction.amount > 0,
        )
        .all()
    )
    tx_ids = [t.plaid_transaction_id for t in tx_rows]
    overrides_by_tx = (
        {
            o.plaid_transaction_id: o
            for o in session.query(TransactionOverride)
            .filter(
                TransactionOverride.user_id == user.id,
                TransactionOverride.plaid_transaction_id.in_(tx_ids),
            )
            .all()
        }
        if tx_ids else {}
    )

    totals: dict[tuple[int, int], float] = defaultdict(float)
    for tx in tx_rows:
        ov = overrides_by_tx.get(tx.plaid_transaction_id)
        if ov and ov.dismissed:
            continue
        category = tx.pfc_primary or "UNKNOWN"
        amount = tx.amount
        if ov:
            if ov.category_override:
                category = ov.category_override
            if ov.amount_override is not None:
                amount = ov.amount_override
        if category in pfc.EXCLUDED_CATEGORIES:
            continue
        totals[(tx.date.year, tx.date.month)] += amount

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


def _rounded_top_path(x: float, y: float, w: float, h: float, r: float = 10.0) -> str:
    """SVG path for a rectangle with only the top corners rounded. Clamps
    the radius so short bars and narrow bars don't produce a malformed path."""
    r = max(0.0, min(r, w / 2, h))
    return (
        f"M {x:.2f},{y + r:.2f} "
        f"Q {x:.2f},{y:.2f} {x + r:.2f},{y:.2f} "
        f"L {x + w - r:.2f},{y:.2f} "
        f"Q {x + w:.2f},{y:.2f} {x + w:.2f},{y + r:.2f} "
        f"L {x + w:.2f},{y + h:.2f} "
        f"L {x:.2f},{y + h:.2f} Z"
    )


def build_monthly_chart(totals: list[dict], width: int = 500, height: int = 150) -> dict | None:
    """SVG bar chart for `monthly_totals`. Each bar carries a pre-rendered
    `path` for a rect with rounded top corners. Returns None when there's
    nothing to plot."""
    if not totals:
        return None

    pad_x = 4
    pad_y = 12
    plot_w = width - 2 * pad_x
    plot_h = height - 2 * pad_y

    values = [t["total"] for t in totals]
    y_max = max(values) if any(values) else 1.0

    n = len(totals)
    bar_gap = 6
    bar_w = (plot_w - bar_gap * (n - 1)) / n

    bars = []
    for i, t in enumerate(totals):
        bar_h = (t["total"] / y_max) * plot_h
        bar_x = pad_x + i * (bar_w + bar_gap)
        bar_y = pad_y + plot_h - bar_h
        bars.append({
            "x": bar_x,
            "y": bar_y,
            "width": bar_w,
            "height": bar_h,
            "path": _rounded_top_path(bar_x, bar_y, bar_w, bar_h),
            "label": t["label"],
            "total": t["total"],
        })

    return {"width": width, "height": height, "bars": bars}


def relative_time(dt: datetime | None) -> str:
    """Render a naive UTC datetime as 'X min/hr/days ago' for the Spending page."""
    if dt is None:
        return "never"
    seconds = (datetime.now(timezone.utc).replace(tzinfo=None) - dt).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        n = int(seconds // 60)
        return f"{n} min ago"
    if seconds < 86400:
        n = int(seconds // 3600)
        return f"{n} hr ago"
    n = int(seconds // 86400)
    return f"{n} day{'s' if n != 1 else ''} ago"
