import hashlib
import logging
import threading

import jwt
import plaid
from plaid.api import plaid_api
from plaid.model.country_code import CountryCode
from plaid.model.institutions_get_by_id_request import InstitutionsGetByIdRequest
from plaid.model.institutions_get_by_id_request_options import InstitutionsGetByIdRequestOptions
from plaid.model.item_get_request import ItemGetRequest
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.item_remove_request import ItemRemoveRequest
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.products import Products
from plaid.model.webhook_verification_key_get_request import WebhookVerificationKeyGetRequest

import config
import providers
from models import PlaidItem, User

log = logging.getLogger(__name__)


def _webhook_url() -> str | None:
    if not config.APP_PUBLIC_URL:
        return None
    return f"{config.APP_PUBLIC_URL.rstrip('/')}/plaid/webhook"


_webhook_key_cache: dict[str, dict] = {}
_webhook_key_lock = threading.Lock()


def _plaid_admin_client() -> plaid_api.PlaidApi | None:
    # Webhook verification needs a real Plaid client; use the configured
    # admin creds if present, else None (verification will reject).
    client_id = config.PLAID_ADMIN_CLIENT_ID
    secret = config.PLAID_ADMIN_SECRET
    if not client_id or not secret:
        return None
    return providers.build_plaid_client(client_id, secret)


def _fetch_webhook_key(key_id: str) -> dict | None:
    with _webhook_key_lock:
        cached = _webhook_key_cache.get(key_id)
        if cached is not None:
            return cached
    client = _plaid_admin_client()
    if client is None:
        return None
    try:
        resp = client.webhook_verification_key_get(
            WebhookVerificationKeyGetRequest(key_id=key_id),
            _request_timeout=providers.PLAID_REQUEST_TIMEOUT_SECONDS,
        )
    except plaid.ApiException:
        log.exception("webhook_verification_key_get failed for key_id=%s", key_id)
        return None
    key = resp.key.to_dict()
    with _webhook_key_lock:
        _webhook_key_cache[key_id] = key
    return key


def verify_webhook(body: bytes, signature_header: str) -> bool:
    # Plaid sends a JWT in the Plaid-Verification header; the JWT carries
    # request_body_sha256 over the raw body. Verify both signature + digest.
    if not signature_header:
        return False
    try:
        unverified_header = jwt.get_unverified_header(signature_header)
    except jwt.InvalidTokenError:
        return False
    if unverified_header.get("alg") != "ES256":
        return False
    key_id = unverified_header.get("kid")
    if not key_id:
        return False
    jwk = _fetch_webhook_key(key_id)
    if not jwk or jwk.get("expired_at") is not None:
        return False
    try:
        public_key = jwt.algorithms.ECAlgorithm.from_jwk(jwk)
        claims = jwt.decode(
            signature_header, public_key, algorithms=["ES256"], leeway=300,
            options={"require": ["iat", "request_body_sha256"]},
        )
    except jwt.InvalidTokenError:
        log.warning("Plaid webhook JWT decode failed")
        return False
    expected_digest = hashlib.sha256(body).hexdigest()
    return claims.get("request_body_sha256") == expected_digest


def create_link_token(client: plaid_api.PlaidApi, user: User) -> str:
    kwargs: dict = dict(
        user=LinkTokenCreateRequestUser(client_user_id=f"user-{user.id}"),
        client_name="Nelly's Wallet",
        products=[Products("transactions")],
        optional_products=[Products("investments")],
        country_codes=[CountryCode("US")],
        language="en",
    )
    webhook = _webhook_url()
    if webhook:
        kwargs["webhook"] = webhook
    req = LinkTokenCreateRequest(**kwargs)
    return client.link_token_create(
        req, _request_timeout=providers.PLAID_REQUEST_TIMEOUT_SECONDS,
    ).link_token


def create_update_link_token(
    client: plaid_api.PlaidApi, user: User, item: PlaidItem,
) -> str:
    kwargs: dict = dict(
        user=LinkTokenCreateRequestUser(client_user_id=f"user-{user.id}"),
        client_name="Nelly's Wallet",
        country_codes=[CountryCode("US")],
        language="en",
        access_token=item.get_access_token(),
    )
    webhook = _webhook_url()
    if webhook:
        kwargs["webhook"] = webhook
    req = LinkTokenCreateRequest(**kwargs)
    return client.link_token_create(
        req, _request_timeout=providers.PLAID_REQUEST_TIMEOUT_SECONDS,
    ).link_token


def lookup_institution(client: plaid_api.PlaidApi, access_token: str) -> dict | None:
    try:
        item_resp = client.item_get(
            ItemGetRequest(access_token=access_token),
            _request_timeout=providers.PLAID_REQUEST_TIMEOUT_SECONDS,
        )
        institution_id = getattr(item_resp.item, "institution_id", None)
        if not institution_id:
            return None
        inst_resp = client.institutions_get_by_id(
            InstitutionsGetByIdRequest(
                institution_id=institution_id,
                country_codes=[CountryCode("US")],
                options=InstitutionsGetByIdRequestOptions(include_optional_metadata=True),
            ),
            _request_timeout=providers.PLAID_REQUEST_TIMEOUT_SECONDS,
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
        ItemPublicTokenExchangeRequest(public_token=public_token),
        _request_timeout=providers.PLAID_REQUEST_TIMEOUT_SECONDS,
    )
    access_token = exchange_response.access_token
    plaid_item_id = exchange_response.item_id

    # public_token is spent; without best-effort item_remove on failure the
    # Plaid-side item is live + billable with no removal path for the user.
    try:
        existing = (
            session.query(PlaidItem)
            .filter_by(user_id=user.id, plaid_item_id=plaid_item_id)
            .one_or_none()
        )
        info = lookup_institution(client, access_token) or {}
        if existing is not None:
            existing.set_access_token(access_token)
            existing.needs_reauth = False
            if info.get("name"):
                existing.institution_name = info["name"]
            if info.get("logo"):
                existing.logo = info["logo"]
            if info.get("url"):
                existing.institution_url = info["url"]
            if info.get("primary_color"):
                existing.primary_color = info["primary_color"]
            session.commit()
            return existing
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
    except Exception:
        log.exception(
            "exchange_and_save failed after token exchange — revoking item %s",
            plaid_item_id,
        )
        try:
            client.item_remove(
                ItemRemoveRequest(access_token=access_token),
                _request_timeout=providers.PLAID_REQUEST_TIMEOUT_SECONDS,
            )
        except Exception:
            log.exception("Best-effort item_remove also failed for %s", plaid_item_id)
        try:
            session.rollback()
        except Exception:
            pass
        raise
