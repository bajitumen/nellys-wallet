"""Spending: locally persisted transactions, per-month aggregation, overrides.

`sync_transactions(user, session)` pulls a date range from Plaid and upserts
into the local `transactions` table — the **only** function that hits Plaid.

`fetch_last_month(user, ...)` reads from the local table, applies user
overrides, aggregates by category. Wrapped in a short-lived in-memory cache so
repeated page loads don't repeat the DB scan.
"""

import threading
import time
from calendar import monthrange
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import plaid
from plaid.model.transactions_get_request import TransactionsGetRequest
from plaid.model.transactions_get_request_options import TransactionsGetRequestOptions

from models import PlaidItem, Transaction, TransactionOverride, User
from providers import plaid_client_for

EXCLUDED_CATEGORIES = {"INCOME", "TRANSFER_IN", "TRANSFER_OUT"}

_CACHE_TTL = 60.0
_cache: dict = {}
_cache_lock = threading.Lock()


def _humanize(category: str) -> str:
    """FOOD_AND_DRINK → Food And Drink."""
    return category.replace("_", " ").title()


def available_sources(user: User) -> list[str]:
    """Institution names of linked items, deduped and sorted."""
    return sorted({(item.institution_name or "Unknown") for item in user.items})


def _resolve_month(month: str | None) -> tuple[str, date, date, str]:
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
            pfc = getattr(tx, "personal_finance_category", None)
            pfc_primary = pfc.primary if pfc and getattr(pfc, "primary", None) else None
            row = existing.get(tx.transaction_id)
            if row is not None:
                row.amount = float(tx.amount or 0)
                row.name = tx.name
                row.merchant_name = getattr(tx, "merchant_name", None)
                row.pfc_primary = pfc_primary
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
                ))
                out["added"] += 1

    user.last_transactions_sync = datetime.now(timezone.utc).replace(tzinfo=None)
    session.commit()
    invalidate_cache(user.id)
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
    month_str, start, end, month_label = _resolve_month(month)
    out: dict = {
        "total": 0.0, "count": 0, "categories": [], "errors": [],
        "source": source, "transactions": [],
        "month": month_str, "month_label": month_label,
        "chart": None,
    }

    if session is None or not user.items:
        return out

    cache_key = (user.id, month_str, source or "_all")
    with _cache_lock:
        cached = _cache.get(cache_key)
    if cached is not None:
        ts, data = cached
        if time.time() - ts < _CACHE_TTL:
            return data

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

    overrides_by_tx: dict[str, TransactionOverride] = {
        o.plaid_transaction_id: o
        for o in session.query(TransactionOverride)
        .filter(TransactionOverride.user_id == user.id)
        .all()
    }

    totals: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    tx_list: list[dict] = []
    for tx in tx_rows:
        category = tx.pfc_primary or "UNKNOWN"
        amount = tx.amount
        split_percentage = None
        ov = overrides_by_tx.get(tx.plaid_transaction_id)
        if ov:
            if ov.category_override:
                category = ov.category_override
            if ov.amount_override is not None:
                amount = ov.amount_override
            split_percentage = ov.split_percentage

        if category in EXCLUDED_CATEGORIES:
            continue
        totals[category] += amount
        counts[category] += 1
        tx_list.append({
            "plaid_id": tx.plaid_transaction_id,
            "date": tx.date,
            "source": items_by_id[tx.item_id].institution_name or "Unknown",
            "name": tx.merchant_name or tx.name or "(no description)",
            "category": _humanize(category),
            "amount": amount,
            "split_percentage": split_percentage,
        })

    out["total"] = sum(totals.values())
    out["count"] = len(tx_list)
    out["categories"] = sorted(
        ({"name": _humanize(k), "total": v, "count": counts[k]} for k, v in totals.items()),
        key=lambda c: -c["total"],
    )
    out["transactions"] = sorted(tx_list, key=lambda t: t["date"], reverse=True)
    out["chart"] = _build_chart(_cumulative_series(tx_list, start, end))

    with _cache_lock:
        _cache[cache_key] = (time.time(), out)
    return out


def _cumulative_series(transactions: list[dict], start: date, end: date) -> list[tuple[date, float]]:
    """Build [(date, running_total)] for the cumulative spending chart.

    Anchors at (start, 0) so every month's chart begins at zero, and extends a
    flat segment to `end` if the last transaction came before the period end —
    keeps the X axis consistent across months. Returns [] when there are no
    transactions to plot."""
    if not transactions:
        return []
    by_date: dict[date, float] = defaultdict(float)
    for tx in transactions:
        by_date[tx["date"]] += tx["amount"]

    series: list[tuple[date, float]] = [(start, 0.0)]
    running = 0.0
    for d in sorted(by_date.keys()):
        running += by_date[d]
        series.append((d, running))
    if series[-1][0] < end:
        series.append((end, running))
    return series


def _build_chart(series: list[tuple[date, float]], width: int = 1000, height: int = 120) -> dict | None:
    """Return SVG `line_path` and `area_path` for the cumulative series, or
    None if there are fewer than 2 points to draw a line between."""
    if len(series) < 2:
        return None

    pad_x = 4
    pad_y = 8
    plot_w = width - 2 * pad_x
    plot_h = height - 2 * pad_y

    x_min = series[0][0].toordinal()
    x_max = series[-1][0].toordinal()
    x_span = max(1, x_max - x_min)
    y_max = max(v for _, v in series)
    y_span = max(1.0, y_max)  # baseline is 0

    def to_x(d: date) -> float:
        return pad_x + (d.toordinal() - x_min) / x_span * plot_w

    def to_y(v: float) -> float:
        return pad_y + (y_max - v) / y_span * plot_h

    points = [(to_x(d), to_y(v)) for d, v in series]
    baseline_y = to_y(0.0)

    line_path = "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in points)
    area_path = " ".join(
        [f"M {points[0][0]:.2f},{baseline_y:.2f}"]
        + [f"L {x:.2f},{y:.2f}" for x, y in points]
        + [f"L {points[-1][0]:.2f},{baseline_y:.2f}", "Z"]
    )

    return {
        "width": width,
        "height": height,
        "line_path": line_path,
        "area_path": area_path,
    }


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
