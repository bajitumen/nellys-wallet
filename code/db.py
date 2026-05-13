"""SQLAlchemy engine and session factory."""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

import config


class Base(DeclarativeBase):
    pass


engine = create_engine(
    config.DATABASE_URL,
    # SQLite-specific: allow connections from multiple threads (Flask dev server).
    connect_args={"check_same_thread": False} if config.DATABASE_URL.startswith("sqlite") else {},
    echo=False,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db():
    """Create all tables. Idempotent — safe to run multiple times.
    Also runs lightweight in-place schema upgrades for SQLite."""
    from sqlalchemy import text
    import models  # noqa: F401 — register models with Base.metadata
    Base.metadata.create_all(engine)

    # Lightweight in-place migrations for existing DBs.
    with engine.connect() as conn:
        ov_cols = {
            row[1] for row in conn.execute(text("PRAGMA table_info(transaction_overrides)"))
        }
        if ov_cols and "split_percentage" not in ov_cols:
            conn.execute(text(
                "ALTER TABLE transaction_overrides ADD COLUMN split_percentage REAL"
            ))

        user_cols = {
            row[1] for row in conn.execute(text("PRAGMA table_info(users)"))
        }
        if user_cols and "last_transactions_sync" not in user_cols:
            conn.execute(text(
                "ALTER TABLE users ADD COLUMN last_transactions_sync TIMESTAMP"
            ))

        conn.commit()
