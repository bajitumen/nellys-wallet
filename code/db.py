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
    """Create all tables. Idempotent — safe to run multiple times."""
    import models  # noqa: F401 — register models with Base.metadata
    Base.metadata.create_all(engine)
