import plaid
from plaid.api import plaid_api
from plaid.model.country_code import CountryCode
from plaid.model.institutions_get_by_id_request import InstitutionsGetByIdRequest
from plaid.model.institutions_get_by_id_request_options import InstitutionsGetByIdRequestOptions
from plaid.model.item_get_request import ItemGetRequest
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products

from models import PlaidItem, User


def create_link_token(client: plaid_api.PlaidApi, user: User) -> str:
    req = LinkTokenCreateRequest(
        user=LinkTokenCreateRequestUser(client_user_id=f"user-{user.id}"),
        client_name="Nelly's Wallet",
        products=[Products("transactions")],
        optional_products=[Products("investments")],
        country_codes=[CountryCode("US")],
        language="en",
    )
    return client.link_token_create(req).link_token


def lookup_institution(client: plaid_api.PlaidApi, access_token: str) -> dict | None:
    # Plaid doesn't ship a logo for every institution; fields besides name may be None.
    try:
        item_resp = client.item_get(ItemGetRequest(access_token=access_token))
        institution_id = getattr(item_resp.item, "institution_id", None)
        if not institution_id:
            return None
        inst_resp = client.institutions_get_by_id(
            InstitutionsGetByIdRequest(
                institution_id=institution_id,
                country_codes=[CountryCode("US")],
                options=InstitutionsGetByIdRequestOptions(include_optional_metadata=True),
            )
        )
        inst = inst_resp.institution
        return {
            "name": inst.name,
            "logo": getattr(inst, "logo", None),
            "url": getattr(inst, "url", None),
            "primary_color": getattr(inst, "primary_color", None),
        }
    except plaid.ApiException:
        return None


def exchange_and_save(
    client: plaid_api.PlaidApi, session, user: User, public_token: str
) -> PlaidItem:
    exchange_response = client.item_public_token_exchange(
        ItemPublicTokenExchangeRequest(public_token=public_token)
    )
    access_token = exchange_response.access_token
    plaid_item_id = exchange_response.item_id

    info = lookup_institution(client, access_token) or {}
    item = PlaidItem(
        user_id=user.id,
        plaid_item_id=plaid_item_id,
        institution_name=info.get("name"),
        logo=info.get("logo"),
        institution_url=info.get("url"),
        primary_color=info.get("primary_color"),
    )
    item.set_access_token(access_token)
    session.add(item)
    session.commit()
    return item
