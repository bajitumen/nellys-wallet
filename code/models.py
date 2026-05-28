from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

import crypto
from db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    clerk_user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(254), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    plaid_client_id_encrypted: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    plaid_secret_encrypted: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)

    # Naive UTC.
    last_transactions_sync: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    monthly_income: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    monthly_spend: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    items: Mapped[list["PlaidItem"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def set_plaid_credentials(self, client_id: str, secret: str) -> None:
        self.plaid_client_id_encrypted = crypto.encrypt(client_id)
        self.plaid_secret_encrypted = crypto.encrypt(secret)

    def get_plaid_credentials(self) -> Optional[tuple[str, str]]:
        if not self.plaid_client_id_encrypted or not self.plaid_secret_encrypted:
            return None
        return (
            crypto.decrypt(self.plaid_client_id_encrypted),
            crypto.decrypt(self.plaid_secret_encrypted),
        )


class PlaidItem(Base):
    __tablename__ = "plaid_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    institution_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Base64 PNG from Plaid, ~1-3KB.
    logo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    institution_url: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    primary_color: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    plaid_item_id: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    access_token_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped[User] = relationship(back_populates="items")

    def set_access_token(self, token: str) -> None:
        self.access_token_encrypted = crypto.encrypt(token)

    def get_access_token(self) -> str:
        return crypto.decrypt(self.access_token_encrypted)


class TransactionOverride(Base):
    __tablename__ = "transaction_overrides"
    __table_args__ = (
        UniqueConstraint("user_id", "plaid_transaction_id", name="uq_override_user_tx"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    plaid_transaction_id: Mapped[str] = mapped_column(String(64), index=True)

    category_override: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    detailed_override: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    amount_override: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    split_percentage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Dismissed rows are excluded from totals; reversible.
    dismissed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 'manual' protects from rule overwrites; 'rule' is recomputed when rules change.
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")


class TransactionRule(Base):
    __tablename__ = "transaction_rules"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "match_field", "match_op", "match_value", "action",
            name="uq_rule_user_field_op_value_action",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # Which Transaction column to match against.
    match_field: Mapped[str] = mapped_column(String(32))
    # 'equals' | 'not_equals' — case-insensitive.
    match_op: Mapped[str] = mapped_column(String(16), nullable=False, default="equals")
    match_value: Mapped[str] = mapped_column(String(256), index=True)
    # 'dismiss' | 'set_category' | 'set_detailed' | 'split'
    action: Mapped[str] = mapped_column(String(32))
    action_value: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AccountRate(Base):
    __tablename__ = "account_rates"
    __table_args__ = (
        UniqueConstraint("user_id", "plaid_account_id", name="uq_rate_user_acct"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    plaid_account_id: Mapped[str] = mapped_column(String(64), index=True)
    rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    monthly_contribution: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class Budget(Base):
    # One row per detailed PFC sub-category; primaries are summed, never stored.
    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint("user_id", "pfc_detailed", name="uq_budget_user_detailed"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    pfc_detailed: Mapped[str] = mapped_column(String(64), index=True)
    amount: Mapped[float] = mapped_column(Float)


class NetWorthSnapshot(Base):
    __tablename__ = "net_worth_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    cash_total: Mapped[float] = mapped_column(Float)
    investment_total: Mapped[float] = mapped_column(Float)
    credit_total: Mapped[float] = mapped_column(Float)
    net_worth: Mapped[float] = mapped_column(Float)


class AccountBalanceSnapshot(Base):
    __tablename__ = "account_balance_snapshots"
    __table_args__ = (
        Index("ix_acct_snap_user_acct_taken", "user_id", "plaid_account_id", "taken_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("plaid_items.id"), index=True)
    plaid_account_id: Mapped[str] = mapped_column(String(64), index=True)
    account_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    institution_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    # cash / credit / investment / other — drives sign at aggregation time.
    bucket: Mapped[str] = mapped_column(String(16))
    taken_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    balance: Mapped[float] = mapped_column(Float)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("user_id", "plaid_transaction_id", name="uq_tx_user_plaid"),
        # Every read filters on (user_id, date); composite avoids row-scans.
        Index("ix_tx_user_date", "user_id", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("plaid_items.id"), index=True)

    plaid_transaction_id: Mapped[str] = mapped_column(String(64), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    amount: Mapped[float] = mapped_column(Float)
    name: Mapped[str] = mapped_column(String(256))
    merchant_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    pfc_primary: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    pfc_detailed: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
