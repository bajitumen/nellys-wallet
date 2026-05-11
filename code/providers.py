"""Plaid fetch layer, now user-scoped.

Each user provides their own Plaid Trial credentials and has their own
linked PlaidItems. Callers pass a User object loaded from the DB.
"""

import plaid
from plaid.api import plaid_api
from plaid.model.accounts_balance_get_request import AccountsBalanceGetRequest
from plaid.model.investments_holdings_get_request import InvestmentsHoldingsGetRequest

from models import User


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


def fetch_all(user: User) -> dict:
    """Fetch balances + holdings for all of `user`'s linked items.
    Returns: {cash, credit, investment, other, holdings, errors}."""
    out = {"cash": [], "credit": [], "investment": [], "other": [],
           "holdings": [], "errors": []}

    if not user.items:
        return out

    try:
        client = plaid_client_for(user)
    except ValueError as e:
        out["errors"].append(str(e))
        return out

    for item in user.items:
        token = item.get_access_token()
        institution = item.institution_name or "Unknown"

        try:
            resp = client.accounts_balance_get(AccountsBalanceGetRequest(access_token=token))
        except plaid.ApiException as e:
            out["errors"].append(
                f"{institution} balances: {getattr(e, 'body', str(e))[:200]}"
            )
            continue

        for acct in resp.accounts:
            bucket = _classify(acct)
            balance = acct.balances.current
            out[bucket].append({
                "institution": institution,
                "name": acct.name,
                "type": str(acct.subtype) if acct.subtype else str(acct.type),
                "mask": acct.mask or "",
                "balance": float(balance) if balance is not None else None,
                "available": (float(acct.balances.available)
                              if acct.balances.available is not None else None),
                "plaid_account_id": acct.account_id,
            })

        try:
            holdings_resp = client.investments_holdings_get(
                InvestmentsHoldingsGetRequest(access_token=token)
            )
            securities = {s.security_id: s for s in holdings_resp.securities}
            for holding in holdings_resp.holdings:
                security = securities.get(holding.security_id)
                out["holdings"].append({
                    "institution": institution,
                    "name": security.name if security else "Unknown",
                    "ticker": getattr(security, "ticker_symbol", None) if security else None,
                    "shares": float(holding.quantity or 0),
                    "price": float(holding.institution_price or 0),
                    "value": float(holding.institution_value or 0),
                })
        except plaid.ApiException as e:
            body = getattr(e, "body", "") or ""
            skip_codes = ("PRODUCT_NOT_READY", "INVESTMENTS_NOT_SUPPORTED",
                          "PRODUCTS_NOT_SUPPORTED", "NO_INVESTMENT_ACCOUNTS",
                          "INVALID_PRODUCT")
            if not any(code in body for code in skip_codes):
                out["errors"].append(f"{institution} holdings: {body[:200]}")

    return out
