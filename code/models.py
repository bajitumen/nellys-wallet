"""Database models. Every Plaid token is stored Fernet-encrypted."""

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

import crypto
from db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    """A user of the app. `clerk_user_id` is the source of truth for identity.
    We mirror minimal profile fields locally for convenience."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    clerk_user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(254), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    # Each user provides their own Plaid Trial credentials (encrypted).
    plaid_client_id_encrypted: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    plaid_secret_encrypted: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)

    # Timestamp of last successful transactions sync (naive UTC). Surfaces as
    # the "Last synced X ago" indicator on the Spending page.
    last_transactions_sync: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

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
    """One linked institution per row. Token is Fernet-encrypted at rest."""

    __tablename__ = "plaid_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    institution_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    plaid_item_id: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    access_token_encrypted: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped[User] = relationship(back_populates="items")

    def set_access_token(self, token: str) -> None:
        self.access_token_encrypted = crypto.encrypt(token)

    def get_access_token(self) -> str:
        return crypto.decrypt(self.access_token_encrypted)


class TransactionOverride(Base):
    """Per-transaction user adjustments layered over Plaid's data at read time.

    Plaid owns the raw transaction; we never mutate it. This row carries the
    bits the user can rewrite: category (e.g. 'this is FOOD_AND_DRINK not
    LOAN_PAYMENTS') and amount (e.g. 'I only paid 1/4 of this dinner').
    """

    __tablename__ = "transaction_overrides"
    __table_args__ = (
        UniqueConstraint("user_id", "plaid_transaction_id", name="uq_override_user_tx"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    plaid_transaction_id: Mapped[str] = mapped_column(String(64), index=True)

    # Raw PFC primary code ("FOOD_AND_DRINK"); humanized only at display time.
    category_override: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    amount_override: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Metadata: % of the original charge the user is responsible for, e.g. 25.0.
    # Optional — manual amount overrides without a split context leave this NULL.
    split_percentage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)


class Transaction(Base):
    """Locally persisted Plaid transaction. Populated by `spending.sync_transactions`;
    read by `spending.fetch_last_month` so that page loads don't hit Plaid.
    Re-syncing upserts by `plaid_transaction_id`."""

    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("user_id", "plaid_transaction_id", name="uq_tx_user_plaid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("plaid_items.id"), index=True)

    plaid_transaction_id: Mapped[str] = mapped_column(String(64), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    amount: Mapped[float] = mapped_column(Float)
    name: Mapped[str] = mapped_column(String(256))
    merchant_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    # Plaid Personal Finance Category, primary tier (e.g. "FOOD_AND_DRINK").
    pfc_primary: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
