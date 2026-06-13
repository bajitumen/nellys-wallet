import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import plaid
from plaid.api import plaid_api
from plaid.model.accounts_get_request import AccountsGetRequest

from models import PlaidItem, User

log = logging.getLogger(__name__)

# accounts_get uses Plaid's cache (~sub-second) vs. accounts_balance_get's
# multi-second live call. Trade-off: balances may lag the bank by hours.
_CACHE_TTL = 90.0
_cache: dict[int, tuple[float, dict]] = {}
_cache_lock = threading.Lock()
_keylocks: dict[int, threading.Lock] = {}


def _get_keylock(user_id: int) -> threading.Lock:
    with _cache_lock:
        lock = _keylocks.get(user_id)
        if lock is None:
            lock = threading.Lock()
            _keylocks[user_id] = lock
        return lock


# US-only: Plaid Link is configured with CountryCode("US"); non-US subtypes never appear.
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
    "ebt": "EBT",
    "ugma": "UGMA",
    "utma": "UTMA",
    "cash management": "Cash Management",
    "money market": "Money Market",
}


def humanize_account_type(raw: str) -> str:
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


# Cap every Plaid HTTP call. Without this, urllib3 waits forever for a
# response, gunicorn's --timeout 90 (a liveness heartbeat under gthread)
# doesn't kill the hung worker, and one stuck bank consumes a thread +
# its executor + the per-user keylock until a health-check restart.
PLAID_REQUEST_TIMEOUT_SECONDS: float = 30.0


def plaid_client_for(user: User) -> plaid_api.PlaidApi:
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
    result: dict = {
        "cash": [], "credit": [], "investment": [], "other": [], "errors": [],
    }
    token = item.get_access_token()
    institution = item.institution_name or "Unknown"
    logo = item.logo
    primary_color = item.primary_color

    try:
        resp = client.accounts_get(
            AccountsGetRequest(access_token=token),
            _request_timeout=PLAID_REQUEST_TIMEOUT_SECONDS,
        )
    except plaid.ApiException as e:
        body = getattr(e, "body", str(e))
        log.warning("accounts_get failed for %s: %s", institution, body[:500])
        # Generic to the client — Plaid error bodies can leak request IDs and
        # the access path; full detail stays in server logs.
        result["errors"].append(f"{institution}: temporarily unavailable.")
        return result

    for acct in resp.accounts:
        result[_classify(acct)].append({
            "institution": institution,
            "logo": logo,
            "primary_color": primary_color,
            "name": acct.name,
            "type": humanize_account_type(
                str(acct.subtype) if acct.subtype else str(acct.type)
            ),
            "mask": acct.mask or "",
            "balance": float(acct.balances.current) if acct.balances.current is not None else None,
            "available": (float(acct.balances.available)
                          if acct.balances.available is not None else None),
            "plaid_account_id": acct.account_id,
            "item_id": item.id,
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
    if not force_refresh:
        cached = _read_cache(user.id)
        if cached is not None:
            return cached

    with _get_keylock(user.id):
        # Re-check post-lock; another thread may have populated the cache.
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
    with _cache_lock:
        _cache.pop(user_id, None)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
        _keylocks.clear()


def source_avatars(user: User) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for item in user.items:
        name = item.institution_name or "Unknown"
        if name in out:
            continue
        out[name] = {
            "logo": item.logo,
            "primary_color": item.primary_color,
        }
    return out


def sum_balances(accounts: list[dict]) -> float:
    return sum(a["balance"] for a in accounts if a.get("balance") is not None)
