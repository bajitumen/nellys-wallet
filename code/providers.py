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

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import plaid
from plaid.api import plaid_api
from plaid.model.accounts_get_request import AccountsGetRequest

from models import PlaidItem, User

_CACHE_TTL = 90.0  # seconds
_cache: dict[int, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


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

    try:
        resp = client.accounts_get(AccountsGetRequest(access_token=token))
    except plaid.ApiException as e:
        result["errors"].append(
            f"{institution} accounts: {getattr(e, 'body', str(e))[:200]}"
        )
        return result

    for acct in resp.accounts:
        result[_classify(acct)].append({
            "institution": institution,
            "name": acct.name,
            "type": str(acct.subtype) if acct.subtype else str(acct.type),
            "mask": acct.mask or "",
            "balance": float(acct.balances.current) if acct.balances.current is not None else None,
            "available": (float(acct.balances.available)
                          if acct.balances.available is not None else None),
            "plaid_account_id": acct.account_id,
        })
    return result


def fetch_all(user: User, force_refresh: bool = False) -> dict:
    """Fetch balances for all of `user`'s linked items in parallel.
    Returns: {cash, credit, investment, other, errors}.
    Cached in-process for _CACHE_TTL seconds per user.id."""
    if not force_refresh:
        with _cache_lock:
            cached = _cache.get(user.id)
        if cached is not None:
            ts, data = cached
            if time.time() - ts < _CACHE_TTL:
                return data

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
