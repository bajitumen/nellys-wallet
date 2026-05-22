from models import AccountRate, User


def get_rates(user: User, session) -> dict[str, float]:
    rows = session.query(AccountRate).filter_by(user_id=user.id).all()
    return {r.plaid_account_id: r.rate for r in rows}


def get_contributions(user: User, session) -> dict[str, float]:
    rows = session.query(AccountRate).filter_by(user_id=user.id).all()
    return {r.plaid_account_id: r.monthly_contribution for r in rows}


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


def upsert_contribution(user: User, plaid_account_id: str, value: float, session) -> None:
    row = (
        session.query(AccountRate)
        .filter_by(user_id=user.id, plaid_account_id=plaid_account_id)
        .one_or_none()
    )
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
    row = (
        session.query(AccountRate)
        .filter_by(user_id=user.id, plaid_account_id=plaid_account_id)
        .one_or_none()
    )
    if row is None:
        return
    if row.monthly_contribution is None:
        session.delete(row)
    else:
        row.rate = None
    session.commit()


def clear_contribution(user: User, plaid_account_id: str, session) -> None:
    row = (
        session.query(AccountRate)
        .filter_by(user_id=user.id, plaid_account_id=plaid_account_id)
        .one_or_none()
    )
    if row is None:
        return
    if row.rate is None:
        session.delete(row)
    else:
        row.monthly_contribution = None
    session.commit()
