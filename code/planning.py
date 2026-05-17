from models import AccountRate, User


def get_rates(user: User, session) -> dict[str, float]:
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
    session.query(AccountRate).filter_by(
        user_id=user.id, plaid_account_id=plaid_account_id,
    ).delete()
    session.commit()
