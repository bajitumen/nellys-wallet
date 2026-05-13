"""
Local dashboard. Reads the current user from the database.

Pre-Clerk: uses the placeholder user (id=1) created by `python code/cli.py seed-me`.
Post-Clerk: will swap in middleware that resolves the authenticated user from
the Clerk session token.
"""

from datetime import date
from functools import wraps

from flask import Flask, g, jsonify, render_template, request

import config
import plaid_link
import providers
import spending as spending_mod
from db import SessionLocal, init_db
from models import TransactionOverride, User

app = Flask(__name__)
app.secret_key = config.FLASK_SECRET_KEY


def current_user(session):
    """Stand-in for Clerk-resolved auth. Returns the first user in the DB."""
    return session.query(User).first()


def with_user(f):
    """Open a DB session, resolve the current user, and pass both to the handler.

    The decorated handler's signature becomes `(session, user, **route_kwargs)`.
    `user` is None when no user has been provisioned — the handler must decide
    what to render in that case. The user is also stashed on flask.g so the
    layout context processor can render header chrome (Refresh/Add) only when
    a user exists."""
    @wraps(f)
    def wrapped(*args, **kwargs):
        with SessionLocal() as session:
            user = current_user(session)
            g.user = user
            return f(session, user, *args, **kwargs)
    return wrapped


@app.context_processor
def inject_layout_globals():
    """Make `g.user` and `last_sync_label` available to every template."""
    user = getattr(g, "user", None)
    return {
        "last_sync_label": spending_mod.relative_time(
            user.last_transactions_sync if user is not None else None
        ),
    }


def _sum_balances(accounts):
    return sum(a["balance"] for a in accounts if a.get("balance") is not None)


def _month_options(n: int = 12) -> list[dict]:
    """The last `n` months as [{value: 'YYYY-MM', label: 'May 2026'}, ...],
    newest first. Used to populate the Spending page's month dropdown."""
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
def add_static_cache_headers(response):
    """Static assets (favicon, etc.) get a 1-day Cache-Control."""
    if request.path.startswith("/static/"):
        response.cache_control.public = True
        response.cache_control.max_age = 86400
    return response


@app.route("/")
@with_user
def dashboard(session, user):
    if user is None:
        return render_template(
            "dashboard.html", active_page="overview", linked=False, no_user=True
        )

    data = providers.fetch_all(user)
    linked = bool(user.items)

    cash_total = _sum_balances(data["cash"])
    investment_total = _sum_balances(data["investment"])
    credit_total = _sum_balances(data["credit"])
    net_total = cash_total + investment_total - credit_total

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
    )


@app.route("/spending")
@with_user
def spending_view(session, user):
    source = request.args.get("source") or None
    month = request.args.get("month") or None
    month_options = _month_options(12)

    if user is None:
        return render_template(
            "spending.html", active_page="spending", linked=False, no_user=True,
            total=0.0, count=0, categories=[], transactions=[], errors=[],
            sources=[], current_source=None,
            month_options=month_options,
            current_month=month_options[0]["value"],
            month_label=month_options[0]["label"],
        )

    sources = spending_mod.available_sources(user)
    if source and source not in sources:
        source = None
    data = spending_mod.fetch_last_month(
        user, month=month, source=source, session=session,
    )
    return render_template(
        "spending.html",
        active_page="spending",
        no_user=False,
        linked=bool(user.items),
        total=data["total"],
        count=data["count"],
        categories=data["categories"],
        transactions=data["transactions"],
        errors=data["errors"],
        sources=sources,
        current_source=source,
        month_options=month_options,
        current_month=data["month"],
        month_label=data["month_label"],
        chart=data["chart"],
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
        return jsonify({"error": str(e)}), 500
    providers.invalidate_cache(user.id)
    return jsonify({"item_id": item.id, "institution_name": item.institution_name})


@app.route("/transactions/<tx_id>/override", methods=["POST"])
@with_user
def transaction_override(session, user, tx_id):
    """Create / update / clear a per-transaction override for the current user.

    Body fields (all optional, partial updates accepted):
      category          str | null      raw PFC code, e.g. "FOOD_AND_DRINK"
      amount            float | null    overrides the displayed dollar amount
      split_percentage  float | null    % of original charge the user owes
      clear             bool            if true, deletes the override row entirely
    """
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
        override.category_override = data["category"] or None
    if "amount" in data:
        override.amount_override = (
            float(data["amount"]) if data["amount"] is not None else None
        )
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


@app.route("/sync", methods=["POST"])
@with_user
def sync_route(session, user):
    """Trigger a Plaid → DB transactions sync for the current user.
    Returns {ok, added, updated, errors}."""
    if user is None:
        return jsonify({"error": "No user"}), 400
    result = spending_mod.sync_transactions(user, session)
    # "Refresh" is also expected to bust the cached account balances on Overview
    providers.invalidate_cache(user.id)
    return jsonify({"ok": True, **result})


if __name__ == "__main__":
    init_db()
    app.run(debug=config.FLASK_ENV == "development", port=5001)
