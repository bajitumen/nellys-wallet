"""Database models. Every Plaid token is stored Fernet-encrypted."""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, LargeBinary, String
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

    items: Mapped[list["PlaidItem"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    snapshots: Mapped[list["Snapshot"]] = relationship(
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


class Snapshot(Base):
    """Append-only balance snapshot. One row per account per fetch.

    TODO: not yet written by any code path. `providers.fetch_all` returns live
    data but does not persist it. Wire up the snapshot writer or drop the table.
    """

    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    item_id: Mapped[Optional[int]] = mapped_column(ForeignKey("plaid_items.id"), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)

    # Account identity
    plaid_account_id: Mapped[str] = mapped_column(String(64), index=True)
    institution: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    account_name: Mapped[str] = mapped_column(String(255))
    account_type: Mapped[str] = mapped_column(String(32))  # cash/credit/investment/other
    account_subtype: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    mask: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)

    # Balances
    balance: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    available: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    user: Mapped[User] = relationship(back_populates="snapshots")
