"""Plaid fetch layer, user-scoped, with a short in-memory cache.

Each user provides their own Plaid Trial credentials and has their own
linked PlaidItems. Callers pass a User object loaded from the DB.

Balances come from `accounts_get` (Plaid-cached, ~sub-second) rather than
`accounts_balance_get` (live institution call, multi-second per item).
Trade-off: cached balances may lag the bank by up to a few hours.

`fetch_all` results are additionally cached in-process for `_CACHE_TTL`
seconds keyed by user.id; consecutive page loads don't re-hit Plaid.
Call `invalidate_cache(user_id)` after mutating user.items (e.g. when a
new PlaidItem is linked).
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import plaid
from plaid.api import plaid_api
from plaid.model.accounts_get_request import AccountsGetRequest

from models import PlaidItem, User

log = logging.getLogger(__name__)

_CACHE_TTL = 90.0  # seconds
_cache: dict[int, tuple[float, dict]] = {}
_cache_lock = threading.Lock()
# Per-user lock so two concurrent cache misses don't both run the work.
_keylocks: dict[int, threading.Lock] = {}


def _get_keylock(user_id: int) -> threading.Lock:
    with _cache_lock:
        lock = _keylocks.get(user_id)
        if lock is None:
            lock = threading.Lock()
            _keylocks[user_id] = lock
        return lock


# Plaid returns account types/subtypes as lowercase strings ("checking",
# "credit card", "ira"). Title-casing works for most, but financial acronyms
# need explicit handling so "ira" doesn't render as "Ira".
_TYPE_OVERRIDES = {
    "ira": "IRA",
    "sep ira": "SEP IRA",
    "simple ira": "SIMPLE IRA",
    "roth ira": "Roth IRA",
    "roth": "Roth",
    "roth 401k": "Roth 401(k)",
    "401k": "401(k)",
    "401a": "401(a)",
    "403b": "403(b)",
    "457b": "457(b)",
    "529": "529",
    "hsa": "HSA",
    "hra": "HRA",
    "cd": "CD",
    "isa": "ISA",
    "tfsa": "TFSA",
    "rrsp": "RRSP",
    "rrif": "RRIF",
    "resp": "RESP",
    "rdsp": "RDSP",
    "ebt": "EBT",
    "gic": "GIC",
    "lif": "LIF",
    "lira": "LIRA",
    "lrif": "LRIF",
    "lrsp": "LRSP",
    "prif": "PRIF",
    "rlif": "RLIF",
    "sarsep": "SARSEP",
    "sipp": "SIPP",
    "ugma": "UGMA",
    "utma": "UTMA",
    "qshr": "QSHR",
    "cash isa": "Cash ISA",
    "cash management": "Cash Management",
    "money market": "Money Market",
}


def humanize_account_type(raw: str) -> str:
    """Display-friendly capitalization for Plaid account types/subtypes.
    Falls back to .title() for anything not in the override map."""
    if not raw:
        return ""
    key = raw.lower().strip()
    if key in _TYPE_OVERRIDES:
        return _TYPE_OVERRIDES[key]
    return raw.title()


def _classify(acct) -> str:
    t = str(acct.type)
    if t == "depository":
        return "cash"
    if t == "credit":
        return "credit"
    if t in ("investment", "brokerage"):
        return "investment"
    return "other"


def plaid_client_for(user: User) -> plaid_api.PlaidApi:
    """Build a Plaid client using the given user's encrypted credentials."""
    creds = user.get_plaid_credentials()
    if not creds:
        raise ValueError(f"User {user.id} has no Plaid credentials configured.")
    client_id, secret = creds
    configuration = plaid.Configuration(
        host=plaid.Environment.Production,
        api_key={"clientId": client_id, "secret": secret},
    )
    return plaid_api.PlaidApi(plaid.ApiClient(configuration))


def _fetch_one(client: plaid_api.PlaidApi, item: PlaidItem) -> dict:
    """Fetch accounts for one item. Returns a per-bucket result dict;
    callers .extend() lists by key."""
    result: dict = {
        "cash": [], "credit": [], "investment": [], "other": [], "errors": [],
    }
    token = item.get_access_token()
    institution = item.institution_name or "Unknown"
    logo = item.logo

    try:
        resp = client.accounts_get(AccountsGetRequest(access_token=token))
    except plaid.ApiException as e:
        body = getattr(e, "body", str(e))
        log.warning("accounts_get failed for %s: %s", institution, body[:500])
        result["errors"].append(f"{institution} accounts: {body[:200]}")
        return result

    for acct in resp.accounts:
        result[_classify(acct)].append({
            "institution": institution,
            "logo": logo,
            "name": acct.name,
            "type": humanize_account_type(
                str(acct.subtype) if acct.subtype else str(acct.type)
            ),
            "mask": acct.mask or "",
            "balance": float(acct.balances.current) if acct.balances.current is not None else None,
            "available": (float(acct.balances.available)
                          if acct.balances.available is not None else None),
            "plaid_account_id": acct.account_id,
        })
    return result


def _read_cache(user_id: int) -> dict | None:
    with _cache_lock:
        cached = _cache.get(user_id)
    if cached is not None:
        ts, data = cached
        if time.time() - ts < _CACHE_TTL:
            return data
    return None


def fetch_all(user: User, force_refresh: bool = False) -> dict:
    """Fetch balances for all of `user`'s linked items in parallel.
    Returns: {cash, credit, investment, other, errors}.
    Cached in-process for _CACHE_TTL seconds per user.id. Concurrent cache
    misses for the same user are serialized so the work runs once."""
    if not force_refresh:
        cached = _read_cache(user.id)
        if cached is not None:
            return cached

    with _get_keylock(user.id):
        # Re-check after acquiring the per-user lock — another thread may
        # have populated the cache while we were waiting.
        if not force_refresh:
            cached = _read_cache(user.id)
            if cached is not None:
                return cached

        out: dict = {
            "cash": [], "credit": [], "investment": [], "other": [], "errors": [],
        }

        items = list(user.items)
        if not items:
            return out

        try:
            client = plaid_client_for(user)
        except ValueError as e:
            out["errors"].append(str(e))
            return out

        with ThreadPoolExecutor(max_workers=min(8, len(items))) as ex:
            results = list(ex.map(lambda it: _fetch_one(client, it), items))

        for r in results:
            for key in ("cash", "credit", "investment", "other", "errors"):
                out[key].extend(r[key])

        with _cache_lock:
            _cache[user.id] = (time.time(), out)
        return out


def invalidate_cache(user_id: int) -> None:
    """Drop a user's cached fetch_all result. Call after user.items mutates."""
    with _cache_lock:
        _cache.pop(user_id, None)


def clear_cache() -> None:
    """Drop the entire cache. Useful for tests."""
    with _cache_lock:
        _cache.clear()
        _keylocks.clear()


def source_logos(user: User) -> dict[str, str]:
    """{institution_name: base64_png_logo} for the user's linked items.
    Used by the Spending/Income source filter tabs to render an inline logo
    alongside the institution name."""
    out: dict[str, str] = {}
    for item in user.items:
        name = item.institution_name or "Unknown"
        if item.logo and name not in out:
            out[name] = item.logo
    return out


def sum_balances(accounts: list[dict]) -> float:
    """Sum the `balance` field across a list of bucketed accounts, ignoring
    entries whose balance is None. Shared by the dashboard and the net-worth
    snapshot capture."""
    return sum(a["balance"] for a in accounts if a.get("balance") is not None)
