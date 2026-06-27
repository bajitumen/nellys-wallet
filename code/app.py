import logging
import math
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, g, jsonify, request, send_from_directory
from flask_wtf.csrf import CSRFProtect, generate_csrf
from sqlalchemy import func, text
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

import auth
import budget as budget_mod
import config
import income as income_mod
import networth as networth_mod
import pfc
import planning as planning_mod
import plaid_link
import providers
import rules as rules_mod
import spending as spending_mod
from db import SessionLocal
from models import (
    AccountBalanceSnapshot, NetWorthSnapshot, PlaidItem, Transaction,
    TransactionOverride, User,
)

log = logging.getLogger(__name__)

if config.SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        sentry_sdk.init(
            dsn=config.SENTRY_DSN,
            integrations=[FlaskIntegration()],
            traces_sample_rate=0.0,
            send_default_pii=False,
        )
    except Exception:
        log.exception("Sentry init failed")

auth.log_clerk_config()

if config.IS_DEVELOPMENT:
    # Auto-migrate on dev boot so pulling new columns doesn't surface as a
    # 500 the first time you hit the page. Prod migrates via entrypoint.sh
    # before gunicorn forks.
    import db as _db
    _db.init_db()

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CLIENT_DIST = _REPO_ROOT / "client" / "dist"


def _invalidate_user_caches(user_id: int) -> None:
    # Override/rule/budget mutations can affect either side (dismiss applies
    # to both, set_category crosses the boundary), so invalidate every cache.
    spending_mod.invalidate_cache(user_id)
    income_mod.invalidate_cache(user_id)
    rules_mod.invalidate_cache(user_id)

app = Flask(__name__)
app.secret_key = config.FLASK_SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=not config.IS_DEVELOPMENT,
    # Clerk __session cookie flags aren't ours to set; the CSRF token IS
    # the cross-site defense. Client retries once on 400/403 with CSRF body.
    WTF_CSRF_TIME_LIMIT=3600,
)

# Render terminates TLS at its proxy; trust exactly one forwarded hop so
# request.is_secure and url_for(_external=True) work.
if config.IS_PRODUCTION:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_for=1, x_host=1)

csrf = CSRFProtect(app)


_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}

# 'unsafe-inline' kept for Clerk's SDK injection + index.html theme bootstrap.
# Prod Clerk lives at clerk.<domain> (custom CNAME) — fold CLERK_FRONTEND_API
# into the allowlist or pk_live_ keys get blocked.
_CLERK_PROD_HOSTS = (
    [f"https://{config.CLERK_FRONTEND_API}"] if config.CLERK_FRONTEND_API else []
)
_CLERK_HOSTS = " ".join([
    "https://*.clerk.accounts.dev",
    "https://*.clerk.com",
    *_CLERK_PROD_HOSTS,
])
# Cloudflare Turnstile = Clerk's bot-protection captcha; missing it on
# script/frame/connect breaks sign-ups that hit the challenge.
_TURNSTILE = "https://challenges.cloudflare.com"
_PLAID_HOSTS = "https://*.plaid.com https://cdn.plaid.com"

_CSP = "; ".join([
    "default-src 'self'",
    f"script-src 'self' 'unsafe-inline' {_CLERK_HOSTS} {_PLAID_HOSTS} {_TURNSTILE}",
    # Clerk's session-refresh Web Worker is a blob: URL; without this the
    # __session cookie silently goes stale on idle tabs.
    "worker-src 'self' blob:",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: https:",
    "font-src 'self' data:",
    f"connect-src 'self' {_CLERK_HOSTS} {_PLAID_HOSTS} {_TURNSTILE}",
    f"frame-src {_CLERK_HOSTS} {_PLAID_HOSTS} {_TURNSTILE}",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
])

if not config.IS_DEVELOPMENT:
    _SECURITY_HEADERS["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

_SECURITY_HEADERS["Content-Security-Policy"] = _CSP


def current_user(session):
    return auth.get_current_user(request, session)


def with_user(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        with SessionLocal() as session:
            user = current_user(session)
            if user is None and auth.clerk_enabled():
                return jsonify({"error": "Not signed in"}), 401
            if (
                user is not None
                and user.get_plaid_credentials() is None
                and request.path != "/api/settings/plaid"
            ):
                return jsonify({
                    "error": "Plaid credentials missing",
                    "setup_required": True,
                }), 409
            g.user = user
            try:
                return f(session, user, *args, **kwargs)
            except HTTPException:
                raise
            except Exception:
                try:
                    session.rollback()
                except Exception:
                    log.exception("Session rollback failed")
                raise
    return wrapped


@app.before_request
def _assign_request_id() -> None:
    g.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]


@app.errorhandler(Exception)
def _on_unhandled_exception(e):
    if isinstance(e, HTTPException):
        return e
    rid = getattr(g, "request_id", "?")
    log.exception("Unhandled exception (request_id=%s, path=%s)", rid, request.path)
    return jsonify({"error": "Internal server error", "request_id": rid}), 500


def _pfc_dropdown_data(side: str = "all") -> dict:
    allowed = set(pfc.primaries_for_side(side))
    primaries = [
        {"code": code, "label": pfc.humanize_primary(code)}
        for code in pfc.ALL_PRIMARIES if code in allowed
    ]
    taxonomy = {
        primary: [
            {"code": code, "label": pfc.humanize_detailed(code, primary)}
            for code in details
        ]
        for primary, details in pfc.PFC_TAXONOMY.items()
        if primary in allowed
    }
    return {"primaries": primaries, "taxonomy": taxonomy}


def _month_options(n: int = 12) -> list[dict]:
    today = date.today()
    out = []
    y, m = today.year, today.month
    for _ in range(n):
        out.append({
            "value": f"{y:04d}-{m:02d}",
            "label": date(y, m, 1).strftime("%B %Y"),
        })
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return out


@app.after_request
def add_response_headers(response):
    for header, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    if request.path.startswith("/assets/"):
        # Vite content-hashes /assets/* — immutable for that filename.
        response.cache_control.public = True
        response.cache_control.max_age = 31536000
        response.cache_control.immutable = True
    elif request.path.startswith("/static/"):
        response.cache_control.public = True
        response.cache_control.max_age = 86400
    return response


@app.route("/link/token", methods=["POST"])
@with_user
def link_token(session, user):
    if user is None:
        return jsonify({"error": "No user provisioned"}), 400
    try:
        client = providers.plaid_client_for(user)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    try:
        token = plaid_link.create_link_token(client, user)
    except Exception:
        log.exception("create_link_token failed for user_id=%s", user.id)
        return jsonify({"error": "Could not start Plaid Link. Try again in a moment."}), 500
    return jsonify({"link_token": token})


@app.route("/link/exchange", methods=["POST"])
@with_user
def link_exchange(session, user):
    public_token = (request.get_json(silent=True) or {}).get("public_token")
    if not public_token:
        return jsonify({"error": "Missing public_token"}), 400
    if user is None:
        return jsonify({"error": "No user provisioned"}), 400
    try:
        client = providers.plaid_client_for(user)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    try:
        item = plaid_link.exchange_and_save(client, session, user, public_token)
    except Exception:
        log.exception("link_exchange failed for user_id=%s", user.id)
        return jsonify({"error": "Could not link this account. Try again in a moment."}), 500
    log.info("link_exchange success user_id=%s institution=%s",
             user.id, item.institution_name)
    providers.invalidate_cache(user.id)
    return jsonify({"item_id": item.id, "institution_name": item.institution_name})


@app.route("/link/token/update/<int:item_id>", methods=["POST"])
@with_user
def link_token_update(session, user, item_id):
    if user is None:
        return jsonify({"error": "No user provisioned"}), 400
    item = session.query(PlaidItem).filter_by(user_id=user.id, id=item_id).one_or_none()
    if item is None:
        return jsonify({"error": "Item not found"}), 404
    try:
        client = providers.plaid_client_for(user)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    try:
        token = plaid_link.create_update_link_token(client, user, item)
    except Exception:
        log.exception("create_update_link_token failed for item_id=%s", item.id)
        return jsonify({"error": "Could not start reauth. Try again in a moment."}), 500
    return jsonify({"link_token": token})


@app.route("/items/<int:item_id>", methods=["DELETE"])
@with_user
def item_delete(session, user, item_id):
    if user is None:
        return jsonify({"error": "No user"}), 400
    item = session.query(PlaidItem).filter_by(user_id=user.id, id=item_id).one_or_none()
    if item is None:
        return jsonify({"error": "Item not found"}), 404
    access_token = None
    try:
        access_token = item.get_access_token()
    except Exception:
        log.warning("Could not decrypt access token for item_id=%s; skipping item_remove", item.id)
    if access_token:
        try:
            client = providers.plaid_client_for(user)
            from plaid.model.item_remove_request import ItemRemoveRequest
            client.item_remove(
                ItemRemoveRequest(access_token=access_token),
                _request_timeout=providers.PLAID_REQUEST_TIMEOUT_SECONDS,
            )
        except Exception:
            log.exception("Best-effort item_remove failed for item_id=%s", item.id)
    session.query(TransactionOverride).filter(
        TransactionOverride.user_id == user.id,
        TransactionOverride.plaid_transaction_id.in_(
            session.query(Transaction.plaid_transaction_id).filter_by(
                user_id=user.id, item_id=item.id,
            )
        ),
    ).delete(synchronize_session=False)
    session.query(Transaction).filter_by(user_id=user.id, item_id=item.id).delete(
        synchronize_session=False,
    )
    session.query(AccountBalanceSnapshot).filter_by(
        user_id=user.id, item_id=item.id,
    ).delete(synchronize_session=False)
    session.delete(item)
    session.commit()
    providers.invalidate_cache(user.id)
    _invalidate_user_caches(user.id)
    return jsonify({"ok": True})


@app.route("/plaid/webhook", methods=["POST"])
@csrf.exempt
def plaid_webhook():
    # Plaid signs webhooks with Plaid-Verification (JWT, ES256). Verify before
    # any state change. plaid_webhook_verify_jwt() handles the key fetch + cache.
    body = request.get_data()
    signature = request.headers.get("Plaid-Verification", "")
    if not plaid_link.verify_webhook(body, signature):
        log.warning("Plaid webhook rejected: signature verification failed")
        return jsonify({"error": "invalid signature"}), 401
    payload = request.get_json(silent=True) or {}
    webhook_type = payload.get("webhook_type")
    webhook_code = payload.get("webhook_code")
    plaid_item_id = payload.get("item_id")
    log.info(
        "Plaid webhook: type=%s code=%s item=%s", webhook_type, webhook_code, plaid_item_id,
    )
    if not plaid_item_id:
        return jsonify({"ok": True})
    needs_reauth_codes = {
        "ITEM_LOGIN_REQUIRED", "PENDING_EXPIRATION", "USER_PERMISSION_REVOKED",
        "USER_ACCOUNT_REVOKED",
    }
    with SessionLocal() as session:
        item = session.query(PlaidItem).filter_by(plaid_item_id=plaid_item_id).one_or_none()
        if item is None:
            return jsonify({"ok": True})
        if webhook_code in needs_reauth_codes:
            item.needs_reauth = True
            session.commit()
            providers.invalidate_cache(item.user_id)
    return jsonify({"ok": True})


@app.route("/transactions/<tx_id>/override", methods=["POST"])
@with_user
def transaction_override(session, user, tx_id):
    data = request.get_json(silent=True) or {}
    if user is None:
        return jsonify({"error": "No user"}), 400

    # Ownership check — without it any user could write override rows
    # keyed by arbitrary plaid_transaction_ids.
    tx_row = (
        session.query(Transaction)
        .filter_by(user_id=user.id, plaid_transaction_id=tx_id)
        .one_or_none()
    )
    if tx_row is None:
        return jsonify({"error": "Transaction not found"}), 404

    override = (
        session.query(TransactionOverride)
        .filter_by(user_id=user.id, plaid_transaction_id=tx_id)
        .one_or_none()
    )

    if data.get("clear"):
        if override is not None:
            session.delete(override)
            session.flush()
        # Re-apply still-matching rules — otherwise a cleared rule-dismiss
        # rejoins totals while the badge still shows.
        rules_mod._recompute_overrides_for_txs(user.id, [tx_row], session)
        session.commit()
        _invalidate_user_caches(user.id)
        return jsonify({"ok": True, "cleared": True})

    if override is None:
        override = TransactionOverride(
            user_id=user.id, plaid_transaction_id=tx_id
        )
        session.add(override)

    if "category" in data:
        cat = data["category"] or None
        if cat is not None and cat not in pfc.ALL_PRIMARIES:
            return jsonify({"error": "Unknown category"}), 400
        override.category_override = cat
    if "detailed" in data:
        detailed = data["detailed"] or None
        if detailed and not pfc.is_valid_detailed(detailed):
            return jsonify({"error": "Unknown detailed code"}), 400
        # Validate against the RESOLVED primary — mismatched detailed codes
        # land in cells _subitems_for never iterates (orphaned amounts).
        resolved_primary = override.category_override or tx_row.pfc_primary
        if detailed and resolved_primary:
            if pfc.primary_of(detailed) != resolved_primary:
                return jsonify({"error": "Detailed code does not belong to chosen category"}), 400
        override.detailed_override = detailed
    # Drop stale detailed override if category was just cleared and the
    # remaining detailed code no longer matches the raw primary.
    if "category" in data and override.category_override is None and override.detailed_override:
        if pfc.primary_of(override.detailed_override) != tx_row.pfc_primary:
            override.detailed_override = None
    if "amount" in data:
        if data["amount"] is None:
            override.amount_override = None
        else:
            try:
                amt = float(data["amount"])
            except (TypeError, ValueError):
                return jsonify({"error": "amount must be a number"}), 400
            if not math.isfinite(amt):
                return jsonify({"error": "amount must be finite"}), 400
            override.amount_override = amt
    if "dismiss" in data:
        override.dismissed = bool(data["dismiss"])
    if "split_percentage" in data:
        if data["split_percentage"] is None:
            override.split_percentage = None
        else:
            try:
                pct = float(data["split_percentage"])
            except (TypeError, ValueError):
                return jsonify({"error": "split_percentage must be a number"}), 400
            if not math.isfinite(pct) or not (0 < pct <= 100):
                return jsonify({"error": "split_percentage must be in (0, 100]"}), 400
            override.split_percentage = pct
    override.source = "manual"

    session.commit()
    _invalidate_user_caches(user.id)
    return jsonify({
        "ok": True,
        "category": override.category_override,
        "amount": override.amount_override,
        "split_percentage": override.split_percentage,
    })


def _validate_condition(c: dict):
    field = c.get("match_field")
    op = c.get("match_op", "equals")
    value = c.get("match_value")
    if field not in rules_mod.VALID_MATCH_FIELDS:
        return None, "Invalid match_field"
    if op not in rules_mod.VALID_MATCH_OPS:
        return None, "Invalid match_op"
    if not value:
        return None, "match_value required"
    if field == "pfc_primary" and value not in pfc.ALL_PRIMARIES:
        return None, "Unknown category match_value"
    if field == "pfc_detailed" and not pfc.is_valid_detailed(value):
        return None, "Unknown detailed match_value"
    return {"match_field": field, "match_op": op, "match_value": value}, None


def _parse_rule_payload(data: dict):
    """Validate a rule payload.

    Accepts either the multi-condition shape ({"conditions": [...], "conditions_logic": ...})
    or the legacy single-condition shape ({"match_field": ..., ...}).
    """
    raw_conditions = data.get("conditions")
    if raw_conditions is None:
        raw_conditions = [{
            "match_field": data.get("match_field"),
            "match_op": data.get("match_op", "equals"),
            "match_value": data.get("match_value"),
        }]
    if not isinstance(raw_conditions, list) or not raw_conditions:
        return None, (jsonify({"error": "At least one condition required"}), 400)

    conditions: list[dict] = []
    for c in raw_conditions:
        parsed, err = _validate_condition(c)
        if err:
            return None, (jsonify({"error": err}), 400)
        conditions.append(parsed)

    conditions_logic = data.get("conditions_logic", "all")
    if conditions_logic not in rules_mod.VALID_LOGIC:
        return None, (jsonify({"error": "Invalid conditions_logic"}), 400)

    action = data.get("action")
    action_value = data.get("action_value")
    scope = data.get("scope", "all")

    if scope not in rules_mod.VALID_SCOPES:
        return None, (jsonify({"error": "Invalid scope"}), 400)
    if action not in rules_mod.VALID_ACTIONS:
        return None, (jsonify({"error": "Invalid action"}), 400)
    if action == "set_category" and action_value and action_value not in pfc.ALL_PRIMARIES:
        return None, (jsonify({"error": "Unknown category"}), 400)
    if action == "set_detailed" and action_value and not pfc.is_valid_detailed(action_value):
        return None, (jsonify({"error": "Unknown detailed code"}), 400)
    if action == "split":
        try:
            pct = float(action_value)
        except (TypeError, ValueError):
            return None, (jsonify({"error": "Split needs a percentage"}), 400)
        if not (0 < pct <= 100):
            return None, (jsonify({"error": "Split percentage must be 0–100"}), 400)
        action_value = str(pct)
    if action == "split_dollar":
        try:
            amt = float(action_value)
        except (TypeError, ValueError):
            return None, (jsonify({"error": "Split needs a dollar amount"}), 400)
        if amt <= 0:
            return None, (jsonify({"error": "Split dollar amount must be positive"}), 400)
        action_value = str(amt)
    return {
        "conditions": conditions, "conditions_logic": conditions_logic,
        "action": action, "action_value": action_value, "scope": scope,
    }, None


@app.route("/rules/preview", methods=["POST"])
@with_user
def rules_preview(session, user):
    if user is None:
        return jsonify({"error": "No user"}), 400
    data = request.get_json(silent=True) or {}
    fields, err = _parse_rule_payload(data)
    if err:
        return err
    txs = rules_mod.query_txs_for_payload(
        user.id, fields["conditions"], fields["conditions_logic"],
        fields["scope"], session,
    )
    return jsonify({"matches": len(txs)})


@app.route("/rules", methods=["POST"])
@with_user
def rules_create(session, user):
    if user is None:
        return jsonify({"error": "No user"}), 400
    data = request.get_json(silent=True) or {}
    fields, err = _parse_rule_payload(data)
    if err:
        return err

    raw_rule_id = data.get("rule_id")
    rule_id: int | None = None
    if raw_rule_id is not None:
        try:
            rule_id = int(raw_rule_id)
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid rule_id"}), 400

    apply_warning = None
    if rule_id is not None:
        from models import TransactionRule
        existing = (
            session.query(TransactionRule)
            .filter_by(id=rule_id, user_id=user.id)
            .one_or_none()
        )
        if existing is None:
            return jsonify({"error": "Rule not found"}), 404
        # Snapshot as IDs — ORM objects can detach across the rule edit.
        old_tx_ids = [t.id for t in rules_mod.snapshot_rule_txs(existing, session)]
        rules_mod.update_rule(
            existing, fields["conditions"], fields["conditions_logic"],
            fields["action"], fields["action_value"], fields["scope"], session,
        )
        rule = existing
        # Rule edit + override reconciliation must commit atomically; a half-
        # applied edit leaves stale overrides that never self-heal.
        try:
            from models import Transaction
            old_txs = (
                session.query(Transaction).filter(Transaction.id.in_(old_tx_ids)).all()
                if old_tx_ids else []
            )
            applied = rules_mod.reapply_after_edit(rule, old_txs, session, force=True)
            session.commit()
        except Exception:
            log.exception("reapply_after_edit failed for rule_id=%s", rule.id)
            session.rollback()
            return jsonify({
                "error": "Could not apply the edited rule to past transactions. Please try again.",
            }), 500
    else:
        rule = rules_mod.create_rule(
            user.id, fields["conditions"], fields["conditions_logic"],
            fields["action"], fields["action_value"], fields["scope"], session,
        )
        # On create there's no stale state to reconcile, so a failed retro
        # apply is recoverable on next sync — commit the rule first.
        session.commit()
        try:
            applied = rules_mod.apply_rule_retroactively(rule, session, force=True)
            session.commit()
        except Exception:
            log.exception("apply_rule_retroactively failed for rule_id=%s", rule.id)
            session.rollback()
            applied = 0
            apply_warning = "Rule saved, but applying to past transactions failed."
    _invalidate_user_caches(user.id)
    out = {"ok": True, "rule_id": rule.id, "applied_to": applied}
    if apply_warning:
        out["warning"] = apply_warning
    return jsonify(out)


def _build_rule_match_options(user, session, side: str = "all") -> dict:
    raw = rules_mod.user_match_options(user, session)
    allowed_primaries = set(pfc.primaries_for_side(side))
    return {
        "merchant": raw["merchant"],
        "category": [
            {"field": o["field"], "value": o["value"], "label": pfc.humanize_primary(o["value"])}
            for o in raw["category"] if o["value"] in allowed_primaries
        ],
        "item": [
            {"field": o["field"], "value": o["value"], "label": pfc.humanize_detailed(o["value"])}
            for o in raw["item"]
            if pfc.primary_of(o["value"]) in allowed_primaries
        ],
        "source": raw.get("source", []),
    }






@app.route("/rules/<int:rule_id>", methods=["DELETE"])
@with_user
def rules_delete(session, user, rule_id):
    if user is None:
        return jsonify({"error": "No user"}), 400
    try:
        ok = rules_mod.delete_rule(user, rule_id, session)
        if not ok:
            return jsonify({"error": "Rule not found"}), 404
        session.commit()
    except Exception:
        log.exception("delete_rule failed for rule_id=%s", rule_id)
        session.rollback()
        return jsonify({"error": "Could not delete the rule. Please try again."}), 500
    _invalidate_user_caches(user.id)
    return jsonify({"ok": True})


@app.route("/planning/rate/<account_id>", methods=["POST"])
@with_user
def planning_rate_save(session, user, account_id):
    if user is None:
        return jsonify({"error": "No user"}), 400
    if not planning_mod.user_owns_account(user, account_id, session):
        return jsonify({"error": "Account not found"}), 404
    data = request.get_json(silent=True) or {}
    rate = data.get("rate")
    if rate in (None, ""):
        planning_mod.clear_rate(user, account_id, session)
        return jsonify({"ok": True, "rate": None})
    try:
        value = float(rate)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid rate"}), 400
    planning_mod.upsert_rate(user, account_id, value, session)
    return jsonify({"ok": True, "rate": value})


@app.route("/planning/contribution/<account_id>", methods=["POST"])
@with_user
def planning_contribution_save(session, user, account_id):
    if user is None:
        return jsonify({"error": "No user"}), 400
    if not planning_mod.user_owns_account(user, account_id, session):
        return jsonify({"error": "Account not found"}), 404
    data = request.get_json(silent=True) or {}
    value = data.get("value")
    if value in (None, ""):
        planning_mod.clear_contribution(user, account_id, session)
        return jsonify({"ok": True, "value": None})
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid value"}), 400
    planning_mod.upsert_contribution(user, account_id, parsed, session)
    return jsonify({"ok": True, "value": parsed})


@app.route("/planning/cashflow", methods=["POST"])
@with_user
def planning_cashflow_save(session, user):
    if user is None:
        return jsonify({"error": "No user"}), 400
    data = request.get_json(silent=True) or {}
    field = data.get("field")
    value = data.get("value")
    if field not in ("income", "spend"):
        return jsonify({"error": "Invalid field"}), 400
    if value in (None, ""):
        parsed = None
    else:
        try:
            parsed = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid value"}), 400
        if parsed < 0:
            return jsonify({"error": "Must be non-negative"}), 400
    if field == "income":
        user.monthly_income = parsed
    else:
        user.monthly_spend = parsed
    session.commit()
    return jsonify({"ok": True, "value": parsed})




@app.route("/budget/<detailed>", methods=["POST"])
@with_user
def budget_save(session, user, detailed):
    if user is None:
        return jsonify({"error": "No user"}), 400
    if not pfc.is_valid_detailed(detailed):
        return jsonify({"error": "Unknown category"}), 400

    data = request.get_json(silent=True) or {}
    amount = data.get("amount")
    if amount in (None, ""):
        budget_mod.clear(user, detailed, session)
    else:
        try:
            value = float(amount)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid amount"}), 400
        if value < 0:
            return jsonify({"error": "Amount must be non-negative"}), 400
        budget_mod.upsert(user, detailed, value, session)

    primary = pfc.primary_of(detailed)
    assert primary is not None
    new_sum = budget_mod.primary_sum(user, primary, session)
    _invalidate_user_caches(user.id)
    return jsonify({"ok": True, "primary_sum": new_sum})


@app.route("/sync", methods=["POST"])
@with_user
def sync_route(session, user):
    if user is None:
        return jsonify({"error": "No user"}), 400
    result = spending_mod.sync_transactions(user, session)
    providers.invalidate_cache(user.id)
    networth_mod.capture(user, session)
    return jsonify({"ok": True, **result})


@app.route("/api/settings/plaid", methods=["GET"])
@with_user
def api_plaid_status(session, user):
    return jsonify({"has_creds": user is not None and user.get_plaid_credentials() is not None})


@app.route("/api/settings/plaid", methods=["POST"])
def api_plaid_save():
    # Hand-rolled auth — @with_user 409s users with no creds, but this is
    # where they SET creds for the first time. Mirror its 401 branch.
    with SessionLocal() as session:
        user = current_user(session)
        if user is None and auth.clerk_enabled():
            return jsonify({"error": "Not signed in"}), 401
        if user is None:
            return jsonify({"error": "No user"}), 400
        data = request.get_json(silent=True) or {}
        client_id = (data.get("plaid_client_id") or "").strip()
        secret = (data.get("plaid_secret") or "").strip()
        if not client_id or not secret:
            return jsonify({"error": "Both fields are required."}), 400
        user.set_plaid_credentials(client_id, secret)
        session.commit()
        providers.invalidate_cache(user.id)
        return jsonify({"ok": True})


@app.route("/api/me")
@with_user
def api_me(session, user):
    last_sync = user.last_transactions_sync if user is not None else None
    return jsonify({
        "last_sync": last_sync.isoformat() if last_sync else None,
        "last_sync_label": spending_mod.relative_time(last_sync),
    })


@app.route("/api/csrf-token")
def api_csrf_token():
    return jsonify({"token": generate_csrf()})


_healthz_cache: dict = {"ts": 0.0, "ok": False}


@app.route("/healthz")
def healthz():
    # Cache a healthy result for 10s so probes don't compete with real traffic
    # under thread saturation. Cheap row read — schema must have users.
    now = time.monotonic()
    if _healthz_cache["ok"] and now - _healthz_cache["ts"] < 10.0:
        return jsonify({"ok": True})
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1 FROM users LIMIT 1")).first()
    except Exception:
        log.exception("/healthz DB probe failed")
        _healthz_cache["ok"] = False
        return jsonify({"ok": False, "reason": "db"}), 503
    if not (_CLIENT_DIST / "index.html").is_file():
        log.error("/healthz SPA shell missing at %s", _CLIENT_DIST)
        return jsonify({"ok": False, "reason": "spa-shell-missing"}), 503
    _healthz_cache["ok"] = True
    _healthz_cache["ts"] = now
    return jsonify({"ok": True})


@app.route("/api/overview")
@with_user
def api_overview(session, user):
    if user is None:
        return jsonify({
            "linked": False, "cash": [], "credit": [], "investment": [], "other": [],
            "errors": [], "cash_total": 0.0, "credit_total": 0.0,
            "investment_total": 0.0, "net_total": 0.0,
            "monthly_cashflow": [], "has_monthly_data": False,
            "networth_chart": None, "networth_snapshot_count": 0,
            "networth_series_data": {}, "networth_series_options": [],
        })
    data = providers.fetch_all(user)
    cash_total = providers.sum_balances(data["cash"])
    investment_total = providers.sum_balances(data["investment"])
    credit_total = providers.sum_balances(data["credit"])
    net_total = cash_total + investment_total - credit_total
    monthly_combined = spending_mod.monthly_cashflow(user, session, n_months=12)
    has_monthly_data = any(m["spend"] > 0 or m["income"] > 0 for m in monthly_combined)

    # Refresh today's snapshot so the chart's latest point matches the cards
    # — they otherwise diverge until the user hits Refresh (which runs /sync,
    # which is the only other path that captures snapshots).
    if user.items and not data.get("errors"):
        try:
            networth_mod.capture(user, session, data=data)
        except Exception:
            log.exception("inline networth.capture failed user_id=%s", user.id)

    snapshots = networth_mod.get_snapshots(user, session)
    now_ts = int(time.time())
    networth_default_start = now_ts - 30 * 86400
    cutoff_dt = datetime.now(timezone.utc).date() - timedelta(days=30)
    networth_default_snapshots = [s for s in snapshots if s.taken_at.date() >= cutoff_dt]
    networth_chart = networth_mod.build_chart(
        networth_default_snapshots,
        range_start_ts=networth_default_start,
        range_end_ts=now_ts,
    )
    account_snaps = networth_mod.get_account_snapshots(user, session)
    series_data = networth_mod.build_series_data(snapshots, account_snaps)
    series_options = [{"key": "net", "label": "Net Worth"}]
    inst_accounts: dict[str, list] = {}
    for bucket in ("cash", "investment", "other"):
        for acct in data[bucket]:
            inst = acct.get("institution") or "Unknown"
            inst_accounts.setdefault(inst, []).append(acct)
    seen_insts: set[str] = set()
    for it in user.items:
        inst = it.institution_name or "Unknown"
        if inst in seen_insts or inst not in inst_accounts:
            continue
        seen_insts.add(inst)
        series_options.append({"key": "inst:" + inst, "label": inst})
        for acct in inst_accounts[inst]:
            series_options.append({
                "key": "acct:" + acct["plaid_account_id"],
                "label": inst + " — " + acct["name"],
                "menu_label": acct["name"],
                "indent": True,
            })
    return jsonify({
        "linked": bool(user.items),
        "cash": data["cash"], "credit": data["credit"],
        "investment": data["investment"], "other": data["other"],
        "errors": data["errors"],
        "cash_total": cash_total, "credit_total": credit_total,
        "investment_total": investment_total, "net_total": net_total,
        "monthly_cashflow": monthly_combined if has_monthly_data else [],
        "has_monthly_data": has_monthly_data,
        "networth_chart": networth_chart,
        "networth_snapshot_count": len(snapshots),
        "networth_series_data": series_data,
        "networth_series_options": series_options,
    })


@app.route("/api/spending")
@with_user
def api_spending(session, user):
    source = request.args.get("source") or None
    month = request.args.get("month") or None
    categories_filter = [
        c for c in request.args.getlist("category") if pfc.is_valid_primary(c)
    ]
    month_options = _month_options(12)
    pfc_data = _pfc_dropdown_data(side="spending")
    if user is None:
        return jsonify({
            "total": 0.0, "count": 0, "categories": [], "transactions": [],
            "errors": [],
            "sources": [], "current_source": None,
            "categories_filter": [], "category_chips": [],
            "month_options": month_options,
            "current_month": month_options[0]["value"],
            "month_label": month_options[0]["label"],
            "daily_avg": 0.0, "prev_month_change_pct": None,
            "primaries": pfc_data["primaries"], "taxonomy": pfc_data["taxonomy"],
            "rule_match_options": {"merchant": [], "category": [], "item": [], "source": []},
            "rules_by_id": {},
        })
    sources = spending_mod.available_sources(user)
    if source and source not in sources:
        source = None
    data = spending_mod.fetch_last_month(
        user, month=month, source=source, session=session,
    )
    month_options = spending_mod.available_months(user, session, source=source)
    rule_match_options = _build_rule_match_options(user, session, side="spending")
    visible_rule_ids = sorted({
        tx["rule_id"] for tx in data["transactions"] if tx.get("rule_id")
    })
    rules_by_id = rules_mod.rules_by_id_dict(user.id, session, visible_rule_ids)
    chips = [
        {"code": c, "label": pfc.humanize_primary(c)} for c in categories_filter
    ]
    return jsonify({
        "total": data["total"], "count": data["count"],
        "categories": data["categories"],
        "transactions": [
            {**t, "date": t["date"].isoformat()} for t in data["transactions"]
        ],
        "errors": data["errors"],
        "sources": sources, "current_source": source,
        "source_logos": providers.source_avatars(user),
        "categories_filter": categories_filter, "category_chips": chips,
        "month_options": month_options,
        "current_month": data["month"], "month_label": data["month_label"],
        "daily_avg": data["daily_avg"],
        "prev_month_change_pct": data["prev_month_change_pct"],
        "primaries": pfc_data["primaries"], "taxonomy": pfc_data["taxonomy"],
        "rule_match_options": rule_match_options,
        "rules_by_id": rules_by_id,
    })


@app.route("/api/income")
@with_user
def api_income(session, user):
    source = request.args.get("source") or None
    month = request.args.get("month") or None
    month_options = _month_options(12)
    pfc_data = _pfc_dropdown_data(side="income")
    if user is None:
        return jsonify({
            "total": 0.0, "count": 0, "payers": [], "transactions": [],
            "sources": [], "current_source": None,
            "month_options": month_options,
            "current_month": month_options[0]["value"],
            "month_label": month_options[0]["label"],
            "daily_avg": 0.0, "prev_month_change_pct": None,
            "primaries": pfc_data["primaries"], "taxonomy": pfc_data["taxonomy"],
            "rule_match_options": {"merchant": [], "category": [], "item": [], "source": []},
            "rules_by_id": {},
        })
    sources = spending_mod.available_sources(user)
    if source and source not in sources:
        source = None
    data = income_mod.fetch_last_month(
        user, month=month, source=source, session=session,
    )
    month_options = income_mod.available_months(user, session, source=source)
    rule_match_options = _build_rule_match_options(user, session, side="income")
    visible_rule_ids = sorted({
        tx["rule_id"] for tx in data["transactions"] if tx.get("rule_id")
    })
    rules_by_id = rules_mod.rules_by_id_dict(user.id, session, visible_rule_ids)
    return jsonify({
        "total": data["total"], "count": data["count"],
        "payers": data["payers"],
        "transactions": [
            {**t, "date": t["date"].isoformat()} for t in data["transactions"]
        ],
        "sources": sources, "current_source": source,
        "source_logos": providers.source_avatars(user),
        "month_options": month_options,
        "current_month": data["month"], "month_label": data["month_label"],
        "daily_avg": data["daily_avg"],
        "prev_month_change_pct": data["prev_month_change_pct"],
        "primaries": pfc_data["primaries"], "taxonomy": pfc_data["taxonomy"],
        "rule_match_options": rule_match_options,
        "rules_by_id": rules_by_id,
    })


@app.route("/api/rules")
@with_user
def api_rules_list(session, user):
    valid_tabs = {"spending", "income", "both"}
    active_tab = request.args.get("tab") or "spending"
    if active_tab not in valid_tabs:
        active_tab = "spending"
    pfc_data = _pfc_dropdown_data()
    if user is None:
        return jsonify({
            "active_tab": active_tab,
            "tab_rules": [],
            "has_rules": False,
            "primaries": pfc_data["primaries"],
            "taxonomy": pfc_data["taxonomy"],
            "rule_match_options": {
                "merchant": [], "category": [], "item": [], "source": [],
            },
            "rules_by_id": {},
        })
    rule_rows = rules_mod.list_rules(user, session)
    rules_by_tab: dict[str, list[dict]] = {"spending": [], "income": [], "both": []}
    for r in rule_rows:
        d = {
            "id": r.id,
            "conditions": rules_mod.condition_labels(r),
            "conditions_logic": r.conditions_logic,
            "action_label": rules_mod.action_label(r),
        }
        rules_by_tab[rules_mod.rule_side(r)].append(d)
    rules_by_id = rules_mod.rules_by_id_dict(
        user.id, session, [r.id for r in rule_rows],
    )
    return jsonify({
        "active_tab": active_tab,
        "tab_rules": rules_by_tab[active_tab],
        "has_rules": bool(rule_rows),
        "primaries": pfc_data["primaries"],
        "taxonomy": pfc_data["taxonomy"],
        "rule_match_options": _build_rule_match_options(user, session),
        "rules_by_id": rules_by_id,
    })


@app.route("/api/budget")
@with_user
def api_budget(session, user):
    month_options = _month_options(12)
    if user is None:
        return jsonify({
            "groups": [], "month_options": month_options,
            "current_month": month_options[0]["value"],
            "month_label": month_options[0]["label"],
            "total_spent": 0.0,
        })
    budgets = budget_mod.get_budgets(user, session)
    month_arg = request.args.get("month")
    spend = spending_mod.fetch_last_month(
        user, month=month_arg, source=None, session=session,
    )
    spent_by_detailed: dict[str, float] = {}
    for c in spend.get("categories", []):
        for s in c.get("subitems", []):
            spent_by_detailed[s["code"]] = s["total"]
    groups = budget_mod.build_groups(budgets, spent_by_detailed)
    return jsonify({
        "groups": groups,
        "month_options": month_options,
        "current_month": spend["month"],
        "month_label": spend["month_label"],
        "total_spent": spend["total"],
    })


@app.route("/api/planning")
@with_user
def api_planning(session, user):
    if user is None:
        return jsonify({
            "accounts": [], "rates": {}, "contributions": {},
            "monthly_income": None, "monthly_spend": None,
            "avg_monthly_income": 0.0, "avg_monthly_spend": 0.0,
        })
    data = providers.fetch_all(user)
    accounts: list[dict] = []
    for bucket, sign in (("cash", 1), ("investment", 1), ("credit", -1), ("other", 1)):
        for acct in data[bucket]:
            if acct.get("balance") is None:
                continue
            accounts.append({
                "id": acct["plaid_account_id"],
                "institution": acct["institution"],
                "logo": acct.get("logo"),
                "primary_color": acct.get("primary_color"),
                "name": acct["name"],
                "type": acct["type"],
                "balance": acct["balance"],
                "bucket": bucket,
                "sign": sign,
            })
    rates = planning_mod.get_rates(user, session)
    contributions = planning_mod.get_contributions(user, session)
    # Reuse the 12-month cache slot — strict subset and avoids a second scan.
    cashflow = spending_mod.monthly_cashflow(user, session, n_months=12)[-6:]
    non_empty = [m for m in cashflow if m["spend"] > 0 or m["income"] > 0]
    if non_empty:
        avg_monthly_income = sum(m["income"] for m in non_empty) / len(non_empty)
        avg_monthly_spend = sum(m["spend"] for m in non_empty) / len(non_empty)
    else:
        avg_monthly_income = 0.0
        avg_monthly_spend = 0.0
    return jsonify({
        "accounts": accounts,
        "rates": rates,
        "contributions": contributions,
        "monthly_income": user.monthly_income,
        "monthly_spend": user.monthly_spend,
        "avg_monthly_income": avg_monthly_income,
        "avg_monthly_spend": avg_monthly_spend,
    })


@app.route("/assets/<path:filename>")
def spa_assets(filename):
    assets_dir = _CLIENT_DIST / "assets"
    if not assets_dir.is_dir():
        return ("client/dist/assets missing", 404)
    return send_from_directory(assets_dir, filename)


def _serve_spa_shell():
    if not (_CLIENT_DIST / "index.html").is_file():
        return ("client/dist/index.html missing", 404)
    return send_from_directory(_CLIENT_DIST, "index.html")


@app.route("/", methods=["GET"])
@app.route("/<path:_path>", methods=["GET"])
def spa_catch_all(_path: str = ""):
    # Single-segment dist/ files (favicon.svg etc.) must serve their real
    # bytes; falling through to index.html breaks the favicon.
    if _path and "/" not in _path:
        candidate = _CLIENT_DIST / _path
        if candidate.is_file():
            return send_from_directory(_CLIENT_DIST, _path)
    return _serve_spa_shell()


if __name__ == "__main__":
    # debug=True + 0.0.0.0 exposes the Werkzeug debugger PIN; never outside dev.
    if not config.IS_DEVELOPMENT:
        raise RuntimeError("app.run() is dev-only; boot prod via gunicorn.")
    app.run(host="0.0.0.0", debug=True, port=5001)
