"""
Local dashboard. Reads the current user from the database.

Pre-Clerk: uses the placeholder user (id=1) created by `python code/cli.py seed-me`.
Post-Clerk: will swap in middleware that resolves the authenticated user from
the Clerk session token.
"""

from flask import Flask, render_template

import config
import providers
from db import SessionLocal, init_db
from models import User

app = Flask(__name__)
app.secret_key = config.FLASK_SECRET_KEY


def current_user(session):
    """Stand-in for Clerk-resolved auth. Returns the first user in the DB."""
    return session.query(User).first()


def _sum_balances(accounts):
    return sum(a["balance"] for a in accounts if a.get("balance") is not None)


@app.route("/")
def dashboard():
    with SessionLocal() as session:
        user = current_user(session)
        if user is None:
            return render_template("dashboard.html", linked=False, no_user=True)

        data = providers.fetch_all(user)
        linked = bool(user.items)

    cash_total = _sum_balances(data["cash"])
    investment_total = _sum_balances(data["investment"])
    credit_total = _sum_balances(data["credit"])
    net_total = cash_total + investment_total - credit_total

    return render_template(
        "dashboard.html",
        cash_accounts=data["cash"],
        credit_accounts=data["credit"],
        investment_accounts=data["investment"],
        other_accounts=data["other"],
        holdings=sorted(data["holdings"], key=lambda h: -h["value"]),
        errors=data["errors"],
        cash_total=cash_total,
        credit_total=credit_total,
        investment_total=investment_total,
        net_total=net_total,
        linked=linked,
        no_user=False,
    )


if __name__ == "__main__":
    init_db()
    app.run(debug=config.FLASK_ENV == "development", port=5001)
