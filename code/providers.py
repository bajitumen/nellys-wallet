import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import plaid
from plaid.api import plaid_api
from plaid.model.accounts_get_request import AccountsGetRequest

import config
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


_PLAID_HOSTS = {
    "production": plaid.Environment.Production,
    "sandbox": plaid.Environment.Sandbox,
}


def _plaid_host():
    # config.PLAID_ENV is validated at import — unknown values raise at startup.
    return _PLAID_HOSTS[config.PLAID_ENV]


def build_plaid_client(client_id: str, secret: str) -> plaid_api.PlaidApi:
    configuration = plaid.Configuration(
        host=_plaid_host(),
        api_key={"clientId": client_id, "secret": secret},
    )
    return plaid_api.PlaidApi(plaid.ApiClient(configuration))


def plaid_client_for(user: User) -> plaid_api.PlaidApi:
    creds = user.get_plaid_credentials()
    if not creds:
        raise ValueError(f"User {user.id} has no Plaid credentials configured.")
    client_id, secret = creds
    return build_plaid_client(client_id, secret)


def _fetch_one(client: plaid_api.PlaidApi, item: PlaidItem) -> dict:
    return _fetch_one_snapshot(
        client, item.id, item.get_access_token(),
        item.institution_name, item.logo, item.primary_color,
    )


def _fetch_one_snapshot(
    client: plaid_api.PlaidApi, item_id: int, token: str,
    institution_name: str | None, logo: str | None, primary_color: str | None,
) -> dict:
    result: dict = {
        "cash": [], "credit": [], "investment": [], "other": [], "errors": [],
    }
    institution = institution_name or "Unknown"

    try:
        resp = client.accounts_get(
            AccountsGetRequest(access_token=token),
            _request_timeout=PLAID_REQUEST_TIMEOUT_SECONDS,
        )
    except plaid.ApiException as e:
        body = getattr(e, "body", str(e))
        log.warning("accounts_get failed for %s: %s", institution, body[:500])
        if "ITEM_LOGIN_REQUIRED" in (body or ""):
            result["errors"].append(f"{institution}: reconnect required.")
            result["needs_reauth_item_id"] = item_id
        else:
            result["errors"].append(f"{institution}: temporarily unavailable.")
        return result
    except Exception as e:
        # Network timeouts / urllib3 errors aren't plaid.ApiException; without
        # catching, they escape ex.map and abort fetch for ALL items.
        log.warning("accounts_get raised non-Plaid error for %s: %s", institution, e)
        result["errors"].append(f"{institution}: temporarily unavailable.")
        return result

    skipped_non_usd = 0
    for acct in resp.accounts:
        iso = getattr(acct.balances, "iso_currency_code", None)
        if isinstance(iso, str) and iso != "USD":
            skipped_non_usd += 1
            continue
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
            "item_id": item_id,
            "iso_currency_code": iso if isinstance(iso, str) else "USD",
        })
    if skipped_non_usd:
        result["errors"].append(
            f"{institution}: hid {skipped_non_usd} non-USD account(s) — totals are USD only."
        )
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

        # Snapshot ORM attributes on the calling thread; the executor reads
        # plain tuples so a SQLAlchemy Session is never shared across threads.
        snapshots = [(it.id, it.get_access_token(), it.institution_name,
                      it.logo, it.primary_color) for it in items]

        def _fetch_snap(snap):
            item_id, token, institution, logo, primary_color = snap
            return _fetch_one_snapshot(client, item_id, token, institution, logo, primary_color)

        with ThreadPoolExecutor(max_workers=min(8, len(items))) as ex:
            results = list(ex.map(_fetch_snap, snapshots))

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
