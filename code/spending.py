import logging
from calendar import monthrange
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import plaid
from plaid.model.transactions_get_request import TransactionsGetRequest
from plaid.model.transactions_get_request_options import TransactionsGetRequestOptions

import budget as budget_mod
import pfc
from cache import KeyedCache
from models import Transaction, TransactionOverride, User
from providers import plaid_client_for

log = logging.getLogger(__name__)

_cache = KeyedCache(ttl_seconds=0.0)


def invalidate_cache(user_id: int) -> None:
    _cache.invalidate(user_id)


def clear_cache() -> None:
    _cache.clear()


def available_sources(user: User) -> list[str]:
    return sorted({(item.institution_name or "Unknown") for item in user.items})


def resolve_month(month: str | None) -> tuple[str, date, date, str]:
    today = date.today()
    try:
        if month is None:
            raise ValueError
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


def previous_month_window(start: date, end: date) -> tuple[date, date]:
    # Day-aligned: shorter prev months are capped to month length.
    if start.month == 1:
        prev_start = date(start.year - 1, 12, 1)
    else:
        prev_start = date(start.year, start.month - 1, 1)
    days = (end - start).days + 1
    prev_month_len = monthrange(prev_start.year, prev_start.month)[1]
    prev_end = date(prev_start.year, prev_start.month, min(days, prev_month_len))
    return prev_start, prev_end


def _apply_spend_override(tx: Transaction, override: TransactionOverride | None):
    if override and override.dismissed:
        return None
    category = tx.pfc_primary or "UNKNOWN"
    amount = tx.amount
    split_percentage = None
    detailed = None
    if override:
        if override.category_override:
            category = override.category_override
        if override.amount_override is not None:
            amount = override.amount_override
        split_percentage = override.split_percentage
        detailed = override.detailed_override
    if category in pfc.EXCLUDED_CATEGORIES:
        return None
    return category, amount, split_percentage, detailed


def _fetch_raw_transactions(client, item, start: date, end: date) -> dict:
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
                pass
            else:
                out["errors"].append(f"{institution} transactions: {body[:200]}")
            break

        out["transactions"].extend(resp.transactions)
        if len(resp.transactions) < page_size:
            break
        offset += page_size

    return out


def sync_transactions(user: User, session, days: int = 90) -> dict:
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
    import income as _income
    _income.invalidate_cache(user.id)
    log.info(
        "sync_transactions user_id=%s added=%s updated=%s errors=%s",
        user.id, out["added"], out["updated"], len(out["errors"]),
    )
    return out


def _load_overrides(user_id: int, tx_ids: list[str], session) -> dict[str, TransactionOverride]:
    if not tx_ids:
        return {}
    return {
        o.plaid_transaction_id: o
        for o in session.query(TransactionOverride)
        .filter(
            TransactionOverride.user_id == user_id,
            TransactionOverride.plaid_transaction_id.in_(tx_ids),
        )
        .all()
    }


def fetch_last_month(
    user: User, month: str | None = None, source: str | None = None, session=None,
) -> dict:
    month_str, start, end, month_label = resolve_month(month)
    empty: dict = {
        "total": 0.0, "count": 0, "categories": [], "errors": [],
        "source": source, "transactions": [],
        "month": month_str, "month_label": month_label,
        "daily_avg": 0.0,
        "prev_month_change_pct": None,
    }

    if session is None or not user.items:
        return empty

    cache_key = (user.id, month_str, source or "_all")
    return _cache.get_or_compute(
        cache_key,
        lambda: _fetch_last_month_uncached(
            user, source, session, start, end, month_str, month_label,
        ),
    )


def _fetch_last_month_uncached(
    user, source, session, start, end, month_str, month_label,
):
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

    prev_start, prev_end = previous_month_window(start, end)
    tx_rows = (
        session.query(Transaction)
        .filter(
            Transaction.user_id == user.id,
            Transaction.date >= prev_start,
            Transaction.date <= end,
            Transaction.item_id.in_(list(items_by_id.keys())),
            Transaction.amount > 0,
        )
        .all()
    )

    overrides_by_tx = _load_overrides(
        user.id, [t.plaid_transaction_id for t in tx_rows], session,
    )

    totals: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    sub_totals: dict[tuple[str, str], float] = defaultdict(float)
    sub_counts: dict[tuple[str, str], int] = defaultdict(int)
    tx_list: list[dict] = []
    prev_total = 0.0
    for tx in tx_rows:
        override = overrides_by_tx.get(tx.plaid_transaction_id)

        # Dismissed: shown for restoring, excluded from aggregates.
        if override and override.dismissed:
            if not (start <= tx.date <= end):
                continue
            category = override.category_override or tx.pfc_primary or "UNKNOWN"
            amount = (
                override.amount_override if override.amount_override is not None
                else tx.amount
            )
            detailed = override.detailed_override
            if detailed is None and tx.pfc_detailed and pfc.primary_of(tx.pfc_detailed) == category:
                detailed = tx.pfc_detailed
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
                "split_percentage": override.split_percentage,
                "dismissed": True,
            })
            continue

        applied = _apply_spend_override(tx, override)
        if applied is None:
            continue
        category, amount, split_percentage, detailed = applied
        if prev_start <= tx.date <= prev_end:
            prev_total += amount
            continue
        if not (start <= tx.date <= end):
            continue
        # Inherit Plaid's detailed only if primary still matches the override.
        if detailed is None and tx.pfc_detailed and pfc.primary_of(tx.pfc_detailed) == category:
            detailed = tx.pfc_detailed

        totals[category] += amount
        counts[category] += 1
        if detailed:
            sub_totals[(category, detailed)] += amount
            sub_counts[(category, detailed)] += 1
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
            "dismissed": False,
        })

    out["total"] = sum(totals.values())
    out["count"] = sum(counts.values())

    primary_budgets: dict[str, float] = defaultdict(float)
    for detailed, amount in budget_mod.get_budgets(user, session).items():
        primary = pfc.primary_of(detailed)
        if primary:
            primary_budgets[primary] += amount

    # Without a source filter, include un-spent primaries so the budget column has rows.
    category_keys = set(totals.keys())
    if source is None:
        category_keys.update(pfc.PFC_TAXONOMY.keys())

    budgets_by_detailed = budget_mod.get_budgets(user, session)

    def _subitems_for(primary: str) -> list[dict]:
        items = [
            {
                "code": detailed,
                "name": pfc.humanize_detailed(detailed, primary),
                "total": sub_totals.get((primary, detailed), 0.0),
                "count": sub_counts.get((primary, detailed), 0),
                "budget": budgets_by_detailed.get(detailed, 0.0),
            }
            for detailed in pfc.PFC_TAXONOMY.get(primary, [])
        ]
        return sorted(items, key=lambda s: (-s["total"], s["name"]))

    out["categories"] = sorted(
        (
            {
                "code": k,
                "name": pfc.humanize_primary(k),
                "total": totals.get(k, 0.0),
                "count": counts.get(k, 0),
                "color": pfc.CATEGORY_COLORS.get(k, pfc.DEFAULT_COLOR),
                "budget": primary_budgets.get(k, 0.0),
                "subitems": _subitems_for(k),
            }
            for k in category_keys
        ),
        key=lambda c: -c["total"],
    )
    out["transactions"] = sorted(tx_list, key=lambda t: t["date"], reverse=True)

    days_elapsed = max(1, (end - start).days + 1)
    out["daily_avg"] = out["total"] / days_elapsed

    if prev_total > 0:
        out["prev_month_change_pct"] = (out["total"] - prev_total) / prev_total * 100

    return out


def monthly_totals(user: User, session, n_months: int = 6) -> list[dict]:
    return [
        {"month": row["month"], "label": row["label"],
         "total": row["spend"], "ts": row["ts"]}
        for row in monthly_cashflow(user, session, n_months=n_months)
    ]


def monthly_cashflow(user: User, session, n_months: int = 6) -> list[dict]:
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
        )
        .all()
    )

    overrides_by_tx = _load_overrides(
        user.id, [t.plaid_transaction_id for t in tx_rows], session,
    )

    spend_by_month: dict[tuple[int, int], float] = defaultdict(float)
    income_by_month: dict[tuple[int, int], float] = defaultdict(float)
    for tx in tx_rows:
        ov = overrides_by_tx.get(tx.plaid_transaction_id)
        if ov and ov.dismissed:
            continue
        key = (tx.date.year, tx.date.month)
        if tx.amount > 0:
            applied = _apply_spend_override(tx, ov)
            if applied is None:
                continue
            _, amount, _, _ = applied
            spend_by_month[key] += amount
        elif tx.amount < 0 and tx.pfc_primary == "INCOME":
            amount = -tx.amount
            if ov and ov.amount_override is not None:
                amount = ov.amount_override
            income_by_month[key] += amount

    out = []
    y, m = start.year, start.month
    for _ in range(n_months):
        key = (y, m)
        out.append({
            "month": f"{y:04d}-{m:02d}",
            "label": date(y, m, 1).strftime("%b %Y"),
            "spend": spend_by_month.get(key, 0.0),
            "income": income_by_month.get(key, 0.0),
            "ts": int(datetime(y, m, 1).timestamp()),
        })
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def relative_time(dt: datetime | None) -> str:
    # dt is naive UTC.
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
