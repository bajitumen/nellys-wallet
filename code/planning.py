from models import AccountBalanceSnapshot, AccountRate, User


def user_owns_account(user: User, plaid_account_id: str, session) -> bool:
    """True iff the user has ever snapshot-recorded this account.

    Without this check the /planning rate/contribution endpoints would write
    an AccountRate row for any string in the URL — scoped to user.id, so not
    a cross-tenant leak, but a DB-growth surface and inconsistent with the
    ownership guard on /transactions/<id>/override.
    """
    return session.query(
        session.query(AccountBalanceSnapshot)
        .filter_by(user_id=user.id, plaid_account_id=plaid_account_id)
        .exists()
    ).scalar()


def _row(user: User, plaid_account_id: str, session) -> AccountRate | None:
    return (
        session.query(AccountRate)
        .filter_by(user_id=user.id, plaid_account_id=plaid_account_id)
        .one_or_none()
    )


def get_rates(user: User, session) -> dict[str, float]:
    rows = session.query(AccountRate).filter_by(user_id=user.id).all()
    return {r.plaid_account_id: r.rate for r in rows}


def get_contributions(user: User, session) -> dict[str, float]:
    rows = session.query(AccountRate).filter_by(user_id=user.id).all()
    return {r.plaid_account_id: r.monthly_contribution for r in rows}


def upsert_rate(user: User, plaid_account_id: str, rate: float, session) -> None:
    row = _row(user, plaid_account_id, session)
    if row is None:
        session.add(AccountRate(
            user_id=user.id, plaid_account_id=plaid_account_id, rate=rate,
        ))
    else:
        row.rate = rate
    session.commit()


def upsert_contribution(user: User, plaid_account_id: str, value: float, session) -> None:
    row = _row(user, plaid_account_id, session)
    if row is None:
        session.add(AccountRate(
            user_id=user.id,
            plaid_account_id=plaid_account_id,
            monthly_contribution=value,
        ))
    else:
        row.monthly_contribution = value
    session.commit()


def clear_rate(user: User, plaid_account_id: str, session) -> None:
    row = _row(user, plaid_account_id, session)
    if row is None:
        return
    if row.monthly_contribution is None:
        session.delete(row)
    else:
        row.rate = None
    session.commit()


def clear_contribution(user: User, plaid_account_id: str, session) -> None:
    row = _row(user, plaid_account_id, session)
    if row is None:
        return
    if row.rate is None:
        session.delete(row)
    else:
        row.monthly_contribution = None
    session.commit()
