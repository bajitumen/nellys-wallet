"""Plaid Link helpers: create link tokens, look up institution names,
and exchange public tokens for access tokens.

Used by both `enroll_plaid.py` (standalone script), `app.py` (the in-app "+"
button flow), and `cli.py` (institution-name backfill).
"""

import plaid
from plaid.api import plaid_api
from plaid.model.country_code import CountryCode
from plaid.model.institutions_get_by_id_request import InstitutionsGetByIdRequest
from plaid.model.item_get_request import ItemGetRequest
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products

from models import PlaidItem, User


def create_link_token(client: plaid_api.PlaidApi, user: User) -> str:
    """Generate a Plaid Link token scoped to this user."""
    req = LinkTokenCreateRequest(
        user=LinkTokenCreateRequestUser(client_user_id=f"user-{user.id}"),
        client_name="Nelly's Wallet",
        products=[Products("transactions")],
        optional_products=[Products("investments")],
        country_codes=[CountryCode("US")],
        language="en",
    )
    return client.link_token_create(req).link_token


def lookup_institution_name(client: plaid_api.PlaidApi, access_token: str) -> str | None:
    """Resolve the friendly institution name for a Plaid item.
    Returns None if Plaid has no institution_id or the call fails."""
    try:
        item_resp = client.item_get(ItemGetRequest(access_token=access_token))
        institution_id = getattr(item_resp.item, "institution_id", None)
        if not institution_id:
            return None
        inst_resp = client.institutions_get_by_id(
            InstitutionsGetByIdRequest(
                institution_id=institution_id,
                country_codes=[CountryCode("US")],
            )
        )
        return inst_resp.institution.name
    except plaid.ApiException:
        return None


def exchange_and_save(
    client: plaid_api.PlaidApi, session, user: User, public_token: str
) -> PlaidItem:
    """Exchange a public_token for an access_token, look up institution name,
    persist a PlaidItem with the encrypted access token. Returns the saved item."""
    exchange_response = client.item_public_token_exchange(
        ItemPublicTokenExchangeRequest(public_token=public_token)
    )
    access_token = exchange_response.access_token
    plaid_item_id = exchange_response.item_id

    item = PlaidItem(
        user_id=user.id,
        plaid_item_id=plaid_item_id,
        institution_name=lookup_institution_name(client, access_token),
    )
    item.set_access_token(access_token)
    session.add(item)
    session.commit()
    return item
