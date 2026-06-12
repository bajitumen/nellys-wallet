"""Cross-user authorization: user B must not see or mutate user A's data.

The conftest force-disables Clerk for tests, so @with_user normally returns
session.query(User).first(). We monkeypatch auth.get_current_user to control
which user the request is acting as.
"""

import pytest

import auth
from models import (
    PlaidItem, Transaction, TransactionOverride, TransactionRule,
    TransactionRuleCondition, User,
)


@pytest.fixture
def two_users(db_session):
    a = User(clerk_user_id="user_a", email="a@x")
    a.set_plaid_credentials("a_cid", "a_secret")
    b = User(clerk_user_id="user_b", email="b@x")
    b.set_plaid_credentials("b_cid", "b_secret")
    db_session.add_all([a, b])
    db_session.commit()
    return a, b


@pytest.fixture
def acting_as(monkeypatch):
    holder = {"user": None}

    def fake(_request, session):
        u = holder["user"]
        return session.query(User).filter_by(id=u.id).one() if u else None

    monkeypatch.setattr(auth, "get_current_user", fake)
    return holder


def _seed_user_data(db_session, user):
    item = PlaidItem(user_id=user.id, institution_name="Bank")
    item.set_access_token("tok")
    db_session.add(item)
    db_session.commit()

    from datetime import date as _date
    tx = Transaction(
        user_id=user.id, item_id=item.id,
        plaid_transaction_id=f"tx_{user.clerk_user_id}",
        date=_date(2026, 6, 1), amount=10.0, name="Coffee",
        pfc_primary="FOOD_AND_DRINK",
    )
    db_session.add(tx)

    rule = TransactionRule(
        user_id=user.id, action="dismiss", scope="all",
        conditions=[TransactionRuleCondition(
            match_field="merchant", match_op="equals", match_value="Coffee",
        )],
    )
    db_session.add(rule)
    db_session.commit()
    return tx, rule


def test_b_cannot_delete_a_rule(client, db_session, two_users, acting_as):
    a, b = two_users
    _, rule_a = _seed_user_data(db_session, a)

    acting_as["user"] = b
    resp = client.delete(f"/rules/{rule_a.id}")
    assert resp.status_code == 404

    surviving = db_session.get(TransactionRule, rule_a.id)
    assert surviving is not None
    assert surviving.user_id == a.id


def test_b_override_on_a_tx_returns_404_and_writes_nothing(client, db_session, two_users, acting_as):
    a, b = two_users
    tx_a, _ = _seed_user_data(db_session, a)

    acting_as["user"] = b
    resp = client.post(
        f"/transactions/{tx_a.plaid_transaction_id}/override",
        json={"dismiss": True},
    )
    # Ownership check rejects B before any override row is created.
    assert resp.status_code == 404

    a_override = (
        db_session.query(TransactionOverride)
        .filter_by(user_id=a.id, plaid_transaction_id=tx_a.plaid_transaction_id)
        .one_or_none()
    )
    assert a_override is None
    b_override = (
        db_session.query(TransactionOverride)
        .filter_by(user_id=b.id, plaid_transaction_id=tx_a.plaid_transaction_id)
        .one_or_none()
    )
    assert b_override is None


def test_b_api_spending_does_not_leak_a_transactions(client, db_session, two_users, acting_as):
    a, b = two_users
    _seed_user_data(db_session, a)
    _seed_user_data(db_session, b)

    acting_as["user"] = b
    resp = client.get("/api/spending?month=2026-06")
    assert resp.status_code == 200
    payload = resp.get_json()
    plaid_ids = {tx["plaid_id"] for tx in payload["transactions"]}
    assert "tx_user_a" not in plaid_ids
    assert "tx_user_b" in plaid_ids


def test_b_api_rules_does_not_leak_a_rules(client, db_session, two_users, acting_as):
    a, b = two_users
    _, rule_a = _seed_user_data(db_session, a)
    _, rule_b = _seed_user_data(db_session, b)

    acting_as["user"] = b
    resp = client.get("/api/rules")
    assert resp.status_code == 200
    rule_ids = set(int(k) for k in resp.get_json()["rules_by_id"].keys())
    assert rule_a.id not in rule_ids
    assert rule_b.id in rule_ids
