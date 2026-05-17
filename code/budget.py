from sqlalchemy import func

import pfc
from models import Budget, User


def get_budgets(user: User, session) -> dict[str, float]:
    rows = session.query(Budget).filter_by(user_id=user.id).all()
    return {b.pfc_detailed: b.amount for b in rows}


def upsert(user: User, detailed: str, amount: float, session) -> None:
    row = (
        session.query(Budget)
        .filter_by(user_id=user.id, pfc_detailed=detailed)
        .one_or_none()
    )
    if row is None:
        session.add(Budget(user_id=user.id, pfc_detailed=detailed, amount=amount))
    else:
        row.amount = amount
    session.commit()


def clear(user: User, detailed: str, session) -> None:
    session.query(Budget).filter_by(
        user_id=user.id, pfc_detailed=detailed
    ).delete()
    session.commit()


def primary_sum(user: User, primary: str, session) -> float:
    codes = pfc.PFC_TAXONOMY.get(primary, [])
    if not codes:
        return 0.0
    result = (
        session.query(func.coalesce(func.sum(Budget.amount), 0.0))
        .filter(Budget.user_id == user.id, Budget.pfc_detailed.in_(codes))
        .scalar()
    )
    return float(result or 0.0)


def build_groups(budgets: dict[str, float]) -> list[dict]:
    groups = []
    for primary, codes in pfc.PFC_TAXONOMY.items():
        subitems = []
        total = 0.0
        for code in codes:
            amount = budgets.get(code, 0.0)
            total += amount
            subitems.append({
                "code": code,
                "label": pfc.humanize_detailed(code, primary),
                "amount": amount,
            })
        groups.append({
            "primary": primary,
            "primary_label": pfc.humanize_primary(primary),
            "color": pfc.CATEGORY_COLORS.get(primary, pfc.DEFAULT_COLOR),
            "total": total,
            "subitems": subitems,
        })
    return groups
