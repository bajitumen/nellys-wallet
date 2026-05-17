import logging
import time
from datetime import date, timedelta
from functools import wraps

from flask import Flask, g, jsonify, redirect, render_template, request
from flask_wtf.csrf import CSRFProtect, generate_csrf

import auth
import budget as budget_mod
import config
import income as income_mod
import networth as networth_mod
import pfc
import planning as planning_mod
import plaid_link
import providers
import spending as spending_mod
from db import SessionLocal, init_db

# init_db at import time so gunicorn workers see tables on first request.
init_db()
from models import TransactionOverride, User

log = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = config.FLASK_SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # Secure cookies require HTTPS; dev server is HTTP.
    SESSION_COOKIE_SECURE=config.FLASK_ENV != "development",
    WTF_CSRF_TIME_LIMIT=None,
)

csrf = CSRFProtect(app)

app.jinja_env.filters["letter_color"] = providers.institution_letter_color


@app.context_processor
def inject_csrf_token():
    return {"csrf_token": generate_csrf}


@app.context_processor
def inject_clerk_config():
    return {
        "clerk_publishable_key": config.CLERK_PUBLISHABLE_KEY,
        "clerk_frontend_api": config.CLERK_FRONTEND_API,
        "clerk_enabled": auth.clerk_enabled(),
    }


_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


def current_user(session):
    return auth.get_current_user(request, session)


_SETUP_PATH = "/settings/plaid"


def with_user(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        with SessionLocal() as session:
            user = current_user(session)
            if user is None and auth.clerk_enabled():
                return redirect("/sign-in")
            if (
                user is not None
                and user.get_plaid_credentials() is None
                and request.path != _SETUP_PATH
            ):
                return redirect(_SETUP_PATH)
            g.user = user
            return f(session, user, *args, **kwargs)
    return wrapped


@app.context_processor
def inject_layout_globals():
    user = getattr(g, "user", None)
    return {
        "last_sync_label": spending_mod.relative_time(
            user.last_transactions_sync if user is not None else None
        ),
    }


def _pfc_dropdown_data() -> dict:
    primaries = [
        {"code": code, "label": pfc.humanize_primary(code)}
        for code in pfc.ALL_PRIMARIES
    ]
    taxonomy = {
        primary: [
            {"code": code, "label": pfc.humanize_detailed(code, primary)}
            for code in details
        ]
        for primary, details in pfc.PFC_TAXONOMY.items()
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
    if request.path.startswith("/static/"):
        response.cache_control.public = True
        response.cache_control.max_age = 86400
    return response


@app.route("/settings/plaid", methods=["GET", "POST"])
@with_user
def plaid_setup(session, user):
    if user is None:
        # Clerk off and no seed user yet — punt home.
        return redirect("/")
    error = None
    if request.method == "POST":
        client_id = (request.form.get("plaid_client_id") or "").strip()
        secret = (request.form.get("plaid_secret") or "").strip()
        if not client_id or not secret:
            error = "Both fields are required."
        else:
            user.set_plaid_credentials(client_id, secret)
            session.commit()
            providers.invalidate_cache(user.id)
            return redirect("/")
    return render_template(
        "plaid_setup.html",
        active_page="settings",
        has_creds=user.get_plaid_credentials() is not None,
        error=error,
    )


@app.route("/settings/plaid/faq")
def plaid_faq():
    return render_template("plaid_faq.html")


@app.route("/sign-in", defaults={"page": "sign-in"})
@app.route("/sign-up", defaults={"page": "sign-up"})
def auth_page(page):
    if not auth.clerk_enabled():
        return redirect("/")
    return render_template(
        "auth_page.html",
        active_page="auth",
        page=page,
    )


@app.route("/")
@with_user
def dashboard(session, user):
    if user is None:
        return render_template(
            "dashboard.html", active_page="overview", linked=False, no_user=True
        )

    data = providers.fetch_all(user)
    linked = bool(user.items)

    cash_total = providers.sum_balances(data["cash"])
    investment_total = providers.sum_balances(data["investment"])
    credit_total = providers.sum_balances(data["credit"])
    net_total = cash_total + investment_total - credit_total

    # Anchor X axis to last 30 days even when data is sparse.
    snapshots = networth_mod.get_snapshots(user, session)
    now_ts = int(time.time())
    networth_default_start = now_ts - 30 * 86400
    cutoff_dt = date.today() - timedelta(days=30)
    networth_default_snapshots = [
        s for s in snapshots if s.taken_at.date() >= cutoff_dt
    ]
    networth_chart = networth_mod.build_chart(
        networth_default_snapshots,
        range_start_ts=networth_default_start,
        range_end_ts=now_ts,
    )

    monthly_combined = spending_mod.monthly_cashflow(user, session, n_months=12)
    has_monthly_data = any(m["spend"] > 0 or m["income"] > 0 for m in monthly_combined)

    return render_template(
        "dashboard.html",
        active_page="overview",
        cash_accounts=data["cash"],
        credit_accounts=data["credit"],
        investment_accounts=data["investment"],
        other_accounts=data["other"],
        errors=data["errors"],
        cash_total=cash_total,
        credit_total=credit_total,
        investment_total=investment_total,
        net_total=net_total,
        linked=linked,
        no_user=False,
        networth_chart=networth_chart,
        networth_snapshot_count=len(snapshots),
        has_monthly_data=has_monthly_data,
        monthly_totals_raw=monthly_combined if has_monthly_data else [],
    )


@app.route("/spending")
@with_user
def spending_view(session, user):
    source = request.args.get("source") or None
    month = request.args.get("month") or None
    categories_filter = [
        c for c in request.args.getlist("category") if pfc.is_valid_primary(c)
    ]
    month_options = _month_options(12)

    pfc_data = _pfc_dropdown_data()
    if user is None:
        return render_template(
            "spending.html", active_page="spending", linked=False, no_user=True,
            total=0.0, count=0, categories=[], transactions=[], errors=[],
            sources=[], source_logos={}, current_source=None,
            categories_filter=[], category_chips=[],
            month_options=month_options,
            current_month=month_options[0]["value"],
            month_label=month_options[0]["label"],
            daily_avg=0.0, prev_month_change_pct=None,
            primaries=pfc_data["primaries"], taxonomy=pfc_data["taxonomy"],
        )

    sources = spending_mod.available_sources(user)
    if source and source not in sources:
        source = None
    data = spending_mod.fetch_last_month(
        user, month=month, source=source, session=session,
    )
    transactions = data["transactions"]
    chips = [
        {"code": c, "label": pfc.humanize_primary(c)} for c in categories_filter
    ]
    return render_template(
        "spending.html",
        active_page="spending",
        no_user=False,
        linked=bool(user.items),
        total=data["total"],
        count=data["count"],
        categories=data["categories"],
        transactions=transactions,
        errors=data["errors"],
        sources=sources,
        source_logos=providers.source_avatars(user),
        current_source=source,
        categories_filter=categories_filter,
        category_chips=chips,
        month_options=month_options,
        current_month=data["month"],
        month_label=data["month_label"],
        daily_avg=data["daily_avg"],
        prev_month_change_pct=data["prev_month_change_pct"],
        primaries=pfc_data["primaries"], taxonomy=pfc_data["taxonomy"],
    )


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
    except Exception as e:
        log.exception("create_link_token failed for user_id=%s", user.id)
        return jsonify({"error": str(e)}), 500
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
    except Exception as e:
        log.exception("link_exchange failed for user_id=%s", user.id)
        return jsonify({"error": str(e)}), 500
    log.info("link_exchange success user_id=%s institution=%s",
             user.id, item.institution_name)
    providers.invalidate_cache(user.id)
    return jsonify({"item_id": item.id, "institution_name": item.institution_name})


@app.route("/transactions/<tx_id>/override", methods=["POST"])
@with_user
def transaction_override(session, user, tx_id):
    data = request.get_json(silent=True) or {}
    if user is None:
        return jsonify({"error": "No user"}), 400

    override = (
        session.query(TransactionOverride)
        .filter_by(user_id=user.id, plaid_transaction_id=tx_id)
        .one_or_none()
    )

    if data.get("clear"):
        if override is not None:
            session.delete(override)
            session.commit()
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
        override.detailed_override = detailed
    if "amount" in data:
        override.amount_override = (
            float(data["amount"]) if data["amount"] is not None else None
        )
    if "dismiss" in data:
        override.dismissed = bool(data["dismiss"])
    if "split_percentage" in data:
        override.split_percentage = (
            float(data["split_percentage"]) if data["split_percentage"] is not None else None
        )

    session.commit()
    spending_mod.invalidate_cache(user.id)
    return jsonify({
        "ok": True,
        "category": override.category_override,
        "amount": override.amount_override,
        "split_percentage": override.split_percentage,
    })


@app.route("/income")
@with_user
def income_view(session, user):
    source = request.args.get("source") or None
    month = request.args.get("month") or None
    month_options = _month_options(12)

    if user is None:
        return render_template(
            "income.html", active_page="income", no_user=True, linked=False,
            total=0.0, count=0, payers=[], transactions=[],
            sources=[], source_logos={}, current_source=None,
            month_options=month_options,
            current_month=month_options[0]["value"],
            month_label=month_options[0]["label"],
            daily_avg=0.0, prev_month_change_pct=None,
        )

    sources = income_mod.available_sources(user)
    if source and source not in sources:
        source = None
    data = income_mod.fetch_last_month(
        user, month=month, source=source, session=session,
    )
    return render_template(
        "income.html",
        active_page="income",
        no_user=False,
        linked=bool(user.items),
        total=data["total"],
        count=data["count"],
        payers=data["payers"],
        transactions=data["transactions"],
        sources=sources,
        source_logos=providers.source_avatars(user),
        current_source=source,
        month_options=month_options,
        current_month=data["month"],
        month_label=data["month_label"],
        daily_avg=data["daily_avg"],
        prev_month_change_pct=data["prev_month_change_pct"],
    )


@app.route("/planning")
@with_user
def planning_view(session, user):
    if user is None:
        return render_template(
            "planning.html", active_page="planning", no_user=True, linked=False,
            accounts=[], rates={},
        )
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
    return render_template(
        "planning.html",
        active_page="planning",
        no_user=False,
        linked=bool(user.items),
        accounts=accounts,
        rates=rates,
        monthly_income=user.monthly_income,
        monthly_spend=user.monthly_spend,
    )


@app.route("/planning/rate/<account_id>", methods=["POST"])
@with_user
def planning_rate_save(session, user, account_id):
    if user is None:
        return jsonify({"error": "No user"}), 400
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


@app.route("/budget")
@with_user
def budget_view(session, user):
    month_options = _month_options(12)
    if user is None:
        return render_template(
            "budget.html", active_page="budget", no_user=True, groups=[],
            month_options=month_options,
            current_month=month_options[0]["value"],
            month_label=month_options[0]["label"],
            total_spent=0.0,
        )
    budgets = budget_mod.get_budgets(user, session)
    groups = budget_mod.build_groups(budgets)
    month_arg = request.args.get("month")
    spend = spending_mod.fetch_last_month(
        user, month=month_arg, source=None, session=session,
    )
    return render_template(
        "budget.html", active_page="budget", no_user=False, groups=groups,
        month_options=month_options,
        current_month=spend["month"],
        month_label=spend["month_label"],
        total_spent=spend["total"],
    )


@app.route("/budget/summary")
@with_user
def budget_summary(session, user):
    if user is None:
        return jsonify({"error": "No user"}), 401
    month_arg = request.args.get("month")
    spend = spending_mod.fetch_last_month(
        user, month=month_arg, source=None, session=session,
    )
    return jsonify({
        "month": spend["month"],
        "month_label": spend["month_label"],
        "total_spent": spend["total"],
    })


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
    assert primary is not None  # is_valid_detailed verified above
    new_sum = budget_mod.primary_sum(user, primary, session)
    # Spending page caches per-primary budget for 60s; bust it.
    spending_mod.invalidate_cache(user.id)
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


if __name__ == "__main__":
    # 0.0.0.0 so phones on same Wi-Fi can reach the dev server.
    app.run(host="0.0.0.0", debug=config.FLASK_ENV == "development", port=5001)
