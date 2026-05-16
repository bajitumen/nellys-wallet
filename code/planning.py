"""Account-level rate storage for the Planning page.

The actual projection math runs in the browser (see static/planning.js) so
the user can flip between accounts and time horizons without a roundtrip.
This module is just persistence: user-set annual rates per Plaid account.
"""

from models import AccountRate, User


def get_rates(user: User, session) -> dict[str, float]:
    """{plaid_account_id: annual_rate_pct} for the user. Accounts without
    a saved rate are simply absent — callers treat absence as 0% (no
    growth, no decay)."""
    rows = session.query(AccountRate).filter_by(user_id=user.id).all()
    return {r.plaid_account_id: r.rate for r in rows}


def upsert_rate(user: User, plaid_account_id: str, rate: float, session) -> None:
    row = (
        session.query(AccountRate)
        .filter_by(user_id=user.id, plaid_account_id=plaid_account_id)
        .one_or_none()
    )
    if row is None:
        session.add(AccountRate(
            user_id=user.id, plaid_account_id=plaid_account_id, rate=rate,
        ))
    else:
        row.rate = rate
    session.commit()


def clear_rate(user: User, plaid_account_id: str, session) -> None:
    """Remove the row entirely — equivalent to "no rate set" again."""
    session.query(AccountRate).filter_by(
        user_id=user.id, plaid_account_id=plaid_account_id,
    ).delete()
    session.commit()
