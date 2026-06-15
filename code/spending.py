import logging
import threading
from calendar import monthrange
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import plaid
from plaid.model.transactions_get_request import TransactionsGetRequest
from plaid.model.transactions_get_request_options import TransactionsGetRequestOptions
from sqlalchemy import func

import budget as budget_mod
import pfc
import rules as rules_mod
import transfers as transfers_mod
from cache import KeyedCache
from models import Transaction, TransactionOverride, User
import providers
from providers import plaid_client_for

# income imports from spending at module load — keep `import income` local.

log = logging.getLogger(__name__)

_cache = KeyedCache(ttl_seconds=60.0)


def invalidate_cache(user_id: int) -> None:
    _cache.invalidate(user_id)


def clear_cache() -> None:
    _cache.clear()


def available_sources(user: User) -> list[str]:
    return sorted({(item.institution_name or "Unknown") for item in user.items})


def items_by_source(user: User, source: str | None = None) -> dict:
    items = {it.id: it for it in user.items}
    if source:
        items = {
            i: it for i, it in items.items()
            if (it.institution_name or "Unknown") == source
        }
    return items


def available_months(
    user: User, session, source: str | None = None, scope: str = "spending",
) -> list[dict]:
    if not user.items:
        return []
    items = items_by_source(user, source)
    if source and not items:
        return []
    # Resolve override per row so the dropdown matches the totals' category
    # routing — without this, a re-categorized row's month appears in totals
    # but not in the dropdown (or vice versa).
    sign_filter = Transaction.amount > 0 if scope == "spending" else Transaction.amount < 0
    tx_rows = (
        session.query(Transaction)
        .filter(
            Transaction.user_id == user.id,
            Transaction.item_id.in_(items),
            sign_filter,
            Transaction.is_internal_transfer.is_(False),
        )
        .all()
    )
    overrides_by_tx = _load_overrides(
        user.id, [t.plaid_transaction_id for t in tx_rows], session,
    )
    in_scope = pfc.is_spend_category if scope == "spending" else pfc.is_strict_income
    months: set[str] = set()
    for tx in tx_rows:
        ov = overrides_by_tx.get(tx.plaid_transaction_id)
        if ov and ov.dismissed:
            continue
        resolved = (ov.category_override if ov and ov.category_override else None) or tx.pfc_primary
        if resolved and in_scope(resolved):
            months.add(tx.date.strftime("%Y-%m"))
    return [
        {"value": m, "label": datetime.strptime(m, "%Y-%m").strftime("%B %Y")}
        for m in sorted(months, reverse=True)
    ]


def resolve_month(month: str | None) -> tuple[str, date, date, str]:
    today = date.today()
    try:
        if month is None:
            raise ValueError
        y_str, m_str = month.split("-")
        y, m = int(y_str), int(m_str)
        if not (1 <= m <= 12):
            raise ValueError
        # Snap future months to current — otherwise end<start collapses
        # previous_month_window into negative-day territory and 500s.
        if (y, m) > (today.year, today.month):
            y, m = today.year, today.month
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
    # Day-aligned; cap at prev-month length, floor day-of-month at 1.
    if start.month == 1:
        prev_start = date(start.year - 1, 12, 1)
    else:
        prev_start = date(start.year, start.month - 1, 1)
    days = max(1, (end - start).days + 1)
    prev_month_len = monthrange(prev_start.year, prev_start.month)[1]
    prev_end = date(prev_start.year, prev_start.month, min(days, prev_month_len))
    return prev_start, prev_end


def _apply_spend_override(tx: Transaction, override: TransactionOverride | None):
    """Resolve (category, amount, split_percentage, detailed, dismissed) for a tx.

    Returns None only when the tx is not spending after overrides; dismissed
    rows resolve normally so callers can show them in the restore list.
    """
    dismissed = bool(override and override.dismissed)
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
    if detailed is None and tx.pfc_detailed and pfc.primary_of(tx.pfc_detailed) == category:
        detailed = tx.pfc_detailed
    if not dismissed and not pfc.is_spend_category(category):
        return None
    return category, amount, split_percentage, detailed, dismissed


def _fetch_raw_transactions_snapshot(
    client, institution: str, token: str, start: date, end: date,
) -> dict:
    out: dict = {"transactions": [], "errors": []}
    page_size = 250
    offset = 0
    # Use total_transactions from the first page as the cap so mid-fetch
    # inserts can't bump us off the end of the page set; pages don't shift
    # because we trust the server-side ordering.
    expected_total: int | None = None

    while True:
        try:
            resp = client.transactions_get(
                TransactionsGetRequest(
                    access_token=token,
                    start_date=start,
                    end_date=end,
                    options=TransactionsGetRequestOptions(count=page_size, offset=offset),
                ),
                _request_timeout=providers.PLAID_REQUEST_TIMEOUT_SECONDS,
            )
        except plaid.ApiException as e:
            body = getattr(e, "body", str(e)) or ""
            log.warning("transactions_get failed for %s: %s", institution, body[:500])
            if "PRODUCT_NOT_READY" in body:
                out["errors"].append(f"{institution}: transactions not yet ready")
            elif "NO_ACCOUNTS" in body or "PRODUCTS_NOT_SUPPORTED" in body:
                pass
            elif "ITEM_LOGIN_REQUIRED" in body or "ITEM_LOCKED" in body:
                out["errors"].append(
                    f"{institution}: reconnect required (your bank logged you out)"
                )
            elif "RATE_LIMIT" in body:
                out["errors"].append(f"{institution}: rate limited, try again shortly")
            elif "INSUFFICIENT_CREDENTIALS" in body or "INVALID_CREDENTIALS" in body:
                out["errors"].append(
                    f"{institution}: reconnect required (credentials changed)"
                )
            elif "INSTITUTION_DOWN" in body or "INSTITUTION_NOT_RESPONDING" in body:
                out["errors"].append(f"{institution}: temporarily unavailable")
            else:
                out["errors"].append(f"{institution}: temporarily unavailable")
            break
        except Exception as e:
            # Network timeouts and urllib3 errors aren't plaid.ApiException;
            # without catching, they'd escape ex.map and abort all items.
            log.warning(
                "transactions_get raised non-Plaid error for %s: %s", institution, e,
            )
            out["errors"].append(f"{institution}: temporarily unavailable")
            break

        out["transactions"].extend(resp.transactions)
        if expected_total is None:
            expected_total = getattr(resp, "total_transactions", None)
        if len(resp.transactions) < page_size:
            break
        offset += page_size
        if expected_total is not None and offset >= expected_total:
            break

    return out


_sync_locks_guard = threading.Lock()
_sync_locks: dict[int, threading.Lock] = {}


def _sync_lock(user_id: int) -> threading.Lock:
    with _sync_locks_guard:
        lock = _sync_locks.get(user_id)
        if lock is None:
            lock = threading.Lock()
            _sync_locks[user_id] = lock
        return lock


def sync_transactions(user: User, session, days: int = 90) -> dict:
    out: dict = {"added": 0, "updated": 0, "errors": []}
    if not user.items:
        return out
    # Serialize per-user — concurrent syncs would double-INSERT under uq_tx_user_plaid.
    lock = _sync_lock(user.id)
    if not lock.acquire(blocking=False):
        out["errors"].append("Sync already in progress.")
        return out
    try:
        return _sync_transactions_locked(user, session, days, out)
    finally:
        lock.release()


def _sync_transactions_locked(user, session, days, out):
    try:
        client = plaid_client_for(user)
    except ValueError as e:
        out["errors"].append(str(e))
        return out

    end = date.today()
    start = end - timedelta(days=days)

    items = list(user.items)
    # Snapshot on the calling thread; never share an ORM attribute load across
    # the executor.
    snapshots = [(it.id, it.institution_name or "Unknown", it.get_access_token())
                 for it in items]

    def _fetch(snap):
        item_id, institution, token = snap
        return (item_id, _fetch_raw_transactions_snapshot(
            client, institution, token, start, end,
        ))

    with ThreadPoolExecutor(max_workers=min(8, len(items))) as ex:
        per_item_raw = list(ex.map(_fetch, snapshots))
    items_by_id = {it.id: it for it in items}
    per_item = [(items_by_id[item_id], result) for item_id, result in per_item_raw]

    # Id-only — date-window filtering would treat Plaid date corrections as
    # missing rows and trip uq_tx_user_plaid on the re-insert.
    existing = {
        t.plaid_transaction_id: t
        for t in session.query(Transaction)
        .filter(Transaction.user_id == user.id)
        .all()
    }
    plaid_ids_seen: set[str] = set()

    new_inserts: list[Transaction] = []
    amount_changed_rows: list[Transaction] = []
    # Only healthy items populate this; window rows absent here are deleted
    # below (Plaid dropped them — reversal, dedup, re-issue under new id).
    seen_ids_by_item: dict[int, set[str]] = {}
    healthy_items: set[int] = set()
    for item, result in per_item:
        out["errors"].extend(result["errors"])
        if not result["errors"]:
            healthy_items.add(item.id)
            seen_ids_by_item.setdefault(item.id, set())
        for tx in result["transactions"]:
            if getattr(tx, "pending", False):
                continue
            # Plaid offset pagination can echo a tx when a new one posts mid-page.
            if tx.transaction_id in plaid_ids_seen:
                continue
            plaid_ids_seen.add(tx.transaction_id)
            if item.id in seen_ids_by_item:
                seen_ids_by_item[item.id].add(tx.transaction_id)
            pending_id = getattr(tx, "pending_transaction_id", None)
            pending_row = existing.get(pending_id) if pending_id else None

            pfc_obj = getattr(tx, "personal_finance_category", None)
            pfc_primary = pfc_obj.primary if pfc_obj and getattr(pfc_obj, "primary", None) else None
            pfc_detailed = (
                pfc_obj.detailed if pfc_obj and getattr(pfc_obj, "detailed", None) else None
            )
            iso_currency = getattr(tx, "iso_currency_code", None)
            if not isinstance(iso_currency, str):
                iso_currency = None
            row = existing.get(tx.transaction_id)
            if row is not None:
                new_amount = float(tx.amount or 0)
                amount_changed = (row.amount != new_amount)
                row.amount = new_amount
                row.name = tx.name
                row.merchant_name = getattr(tx, "merchant_name", None)
                row.pfc_primary = pfc_primary
                row.pfc_detailed = pfc_detailed
                row.iso_currency_code = iso_currency
                row.item_id = item.id
                if tx.date is not None and row.date != tx.date:
                    row.date = tx.date
                out["updated"] += 1
                if amount_changed:
                    amount_changed_rows.append(row)
            else:
                # Move pending → posted override; drop any preexisting one
                # at the posted id to avoid uq_override_user_tx. Flush between
                # the bulk delete and the rename — synchronize_session=False
                # bypasses the identity map, so an in-memory override at the
                # posted id could otherwise be re-flushed and collide.
                if pending_row is not None:
                    session.query(TransactionOverride).filter(
                        TransactionOverride.user_id == user.id,
                        TransactionOverride.plaid_transaction_id == tx.transaction_id,
                    ).delete(synchronize_session="fetch")
                    session.flush()
                    session.query(TransactionOverride).filter_by(
                        user_id=user.id, plaid_transaction_id=pending_id,
                    ).update(
                        {"plaid_transaction_id": tx.transaction_id},
                        synchronize_session="fetch",
                    )
                    session.flush()
                new_tx = Transaction(
                    user_id=user.id,
                    item_id=item.id,
                    plaid_transaction_id=tx.transaction_id,
                    date=tx.date,
                    amount=float(tx.amount or 0),
                    iso_currency_code=iso_currency,
                    name=tx.name,
                    merchant_name=getattr(tx, "merchant_name", None),
                    pfc_primary=pfc_primary,
                    pfc_detailed=pfc_detailed,
                )
                session.add(new_tx)
                new_inserts.append(new_tx)
                existing[tx.transaction_id] = new_tx
                out["added"] += 1
            if pending_row is not None:
                session.delete(pending_row)
                existing.pop(pending_id, None)

    # Reconcile-and-delete rows healthy items stopped returning (reversal,
    # dedup, re-issue). Skip errored items — can't distinguish deleted
    # from missed.
    removed_count = 0
    if healthy_items:
        in_window = (
            session.query(Transaction)
            .filter(
                Transaction.user_id == user.id,
                Transaction.item_id.in_(healthy_items),
                Transaction.date >= start,
                Transaction.date <= end,
            )
            .all()
        )
        for row in in_window:
            seen = seen_ids_by_item.get(row.item_id, set())
            if row.plaid_transaction_id in seen:
                continue
            # Still acknowledged under a different item_id (item shift since
            # last sync) — keep it.
            if row.plaid_transaction_id in plaid_ids_seen:
                continue
            session.query(TransactionOverride).filter_by(
                user_id=user.id, plaid_transaction_id=row.plaid_transaction_id,
            ).delete(synchronize_session=False)
            session.delete(row)
            removed_count += 1
    if removed_count:
        out["removed"] = removed_count

    rules_mod.apply_rules_to_new_transactions(user.id, new_inserts, session)
    # Recompute split overrides — a 50%-of-$100 stored value no longer
    # matches after Plaid corrects the amount to $120.
    if amount_changed_rows:
        rules_mod._recompute_overrides_for_txs(user.id, amount_changed_rows, session)

    transfers_mod.pair_internal_transfers(user.id, session)

    user.last_transactions_sync = datetime.now(timezone.utc).replace(tzinfo=None)
    session.commit()
    invalidate_cache(user.id)
    import income as _income
    _income.invalidate_cache(user.id)
    rules_mod.invalidate_cache(user.id)
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

    items_by_id = items_by_source(user, source)
    if source and not items_by_id:
        return out

    prev_start, prev_end = previous_month_window(start, end)
    tx_rows = (
        session.query(Transaction)
        .filter(
            Transaction.user_id == user.id,
            Transaction.date >= prev_start,
            Transaction.date <= end,
            Transaction.item_id.in_(items_by_id),
            *rules_mod.build_scope_filter("spending"),
            Transaction.is_internal_transfer.is_(False),
        )
        .all()
    )

    overrides_by_tx = _load_overrides(
        user.id, [t.plaid_transaction_id for t in tx_rows], session,
    )
    rule_id_by_tx = rules_mod.applied_rule_id_by_tx(tx_rows, user.id, session)

    totals: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    sub_totals: dict[tuple[str, str], float] = defaultdict(float)
    sub_counts: dict[tuple[str, str], int] = defaultdict(int)
    tx_list: list[dict] = []
    prev_total = 0.0
    skipped_non_usd = 0
    for tx in tx_rows:
        if tx.iso_currency_code and tx.iso_currency_code != "USD":
            skipped_non_usd += 1
            continue
        override = overrides_by_tx.get(tx.plaid_transaction_id)
        resolved = _apply_spend_override(tx, override)
        if resolved is None:
            continue
        category, amount, split_percentage, detailed, dismissed = resolved

        if dismissed:
            if not (start <= tx.date <= end):
                continue
        else:
            if prev_start <= tx.date <= prev_end:
                prev_total += amount
                continue
            if not (start <= tx.date <= end):
                continue
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
            "original_amount": tx.amount,
            "split_percentage": split_percentage,
            "dismissed": dismissed,
            "rule_id": rule_id_by_tx.get(tx.plaid_transaction_id),
        })

    # Round at the aggregation boundary; float drift otherwise prints 1234.5600000001.
    out["total"] = round(sum(totals.values()), 2)
    out["count"] = sum(counts.values())

    budgets_by_detailed = budget_mod.get_budgets(user, session)
    primary_budgets: dict[str, float] = defaultdict(float)
    for detailed, amount in budgets_by_detailed.items():
        primary = pfc.primary_of(detailed)
        if primary:
            primary_budgets[primary] += amount

    # Always include every spend-side primary so the table renders $0 rows;
    # never include INCOME / TRANSFER_IN.
    category_keys = {
        p for p in pfc.PFC_TAXONOMY.keys() if pfc.is_spend_category(p)
    }
    category_keys.update(k for k in totals.keys() if pfc.is_spend_category(k))

    def _subitems_for(primary: str) -> list[dict]:
        items = [
            {
                "code": detailed,
                "name": pfc.humanize_detailed(detailed, primary),
                "total": round(sub_totals.get((primary, detailed), 0.0), 2),
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
                "total": round(totals.get(k, 0.0), 2),
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
    out["daily_avg"] = round(out["total"] / days_elapsed, 2)

    if prev_total > 0:
        out["prev_month_change_pct"] = round((out["total"] - prev_total) / prev_total * 100, 1)
    if skipped_non_usd:
        out.setdefault("errors", []).append(
            f"Hid {skipped_non_usd} transaction(s) in non-USD currencies — totals are USD only."
        )

    return out


def monthly_totals(user: User, session, n_months: int = 6) -> list[dict]:
    return [
        {"month": row["month"], "label": row["label"],
         "total": row["spend"], "ts": row["ts"]}
        for row in monthly_cashflow(user, session, n_months=n_months)
    ]


def monthly_cashflow(user: User, session, n_months: int = 6) -> list[dict]:
    # /api/overview hot path; invalidated by every override/sync/rule mutation.
    return _cache.get_or_compute(
        (user.id, "cashflow", n_months),
        lambda: _monthly_cashflow_uncached(user, session, n_months=n_months),
    )


def _monthly_cashflow_uncached(user: User, session, n_months: int = 6) -> list[dict]:
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
    import income as _income_mod
    for tx in tx_rows:
        if tx.is_internal_transfer:
            continue
        if tx.iso_currency_code and tx.iso_currency_code != "USD":
            continue
        ov = overrides_by_tx.get(tx.plaid_transaction_id)
        if ov and ov.dismissed:
            continue
        if tx.amount is None:
            continue
        key = (tx.date.year, tx.date.month)
        # Sign guards must match build_scope_filter() — pages enforce
        # amount>0 for spend, amount<0 for income; refunds and reversals
        # would otherwise flow through and disagree with page totals.
        resolved_category = (
            (ov.category_override if ov and ov.category_override else None)
            or tx.pfc_primary
            or "UNKNOWN"
        )
        if tx.amount < 0 and pfc.is_strict_income(resolved_category):
            income_by_month[key] += _income_mod.income_amount_with_override(tx.amount, ov)
        elif tx.amount > 0 and pfc.is_spend_category(resolved_category):
            applied = _apply_spend_override(tx, ov)
            if applied is None:
                continue
            _, amount, _, _, _ = applied
            spend_by_month[key] += amount

    out = []
    y, m = start.year, start.month
    for _ in range(n_months):
        key = (y, m)
        out.append({
            "month": f"{y:04d}-{m:02d}",
            "label": date(y, m, 1).strftime("%b %Y"),
            "spend": round(spend_by_month.get(key, 0.0), 2),
            "income": round(income_by_month.get(key, 0.0), 2),
            # UTC so the client's range cutoffs land consistently.
            "ts": int(datetime(y, m, 1, tzinfo=timezone.utc).timestamp()),
        })
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def relative_time(dt: datetime | None) -> str:
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
