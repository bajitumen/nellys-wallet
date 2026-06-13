import os
import stat

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

import config


def _restrict_sqlite_perms() -> None:
    # SQLite defaults world-readable; budgets/snapshots/overrides leak.
    if not config.DATABASE_URL.startswith("sqlite:///"):
        return
    path = config.DATABASE_URL[len("sqlite:///") :]
    if not os.path.exists(path):
        return
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


class Base(DeclarativeBase):
    pass


engine = create_engine(
    config.DATABASE_URL,
    connect_args={"check_same_thread": False} if config.DATABASE_URL.startswith("sqlite") else {},
    echo=False,
)


def _is_file_backed_sqlite(url: str) -> bool:
    if not url.startswith("sqlite"):
        return False
    if url in ("sqlite://", "sqlite:///") or url.endswith(":memory:"):
        return False
    return True


if config.DATABASE_URL.startswith("sqlite"):
    # WAL needs sidecar files (-wal, -shm); back up via sqlite3.backup or
    # VACUUM INTO, never `cp`. Local disk only — fcntl breaks on NFS/SMB.
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):
        cur = dbapi_connection.cursor()
        if _is_file_backed_sqlite(config.DATABASE_URL):
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db():
    # Hand-rolled idempotent migrations; alembic in pyproject is intentionally
    # unused for this single-file SQLite DB. KeyedCache assumes -w 1 — adding
    # workers reintroduces silent cross-worker staleness.
    from sqlalchemy import text
    import models  # noqa: F401
    Base.metadata.create_all(engine)

    with engine.connect() as conn:
        ov_cols = {
            row[1] for row in conn.execute(text("PRAGMA table_info(transaction_overrides)"))
        }
        if ov_cols and "split_percentage" not in ov_cols:
            conn.execute(text(
                "ALTER TABLE transaction_overrides ADD COLUMN split_percentage REAL"
            ))
        if ov_cols and "detailed_override" not in ov_cols:
            conn.execute(text(
                "ALTER TABLE transaction_overrides ADD COLUMN detailed_override VARCHAR(64)"
            ))
        if ov_cols and "dismissed" not in ov_cols:
            conn.execute(text(
                "ALTER TABLE transaction_overrides ADD COLUMN dismissed BOOLEAN NOT NULL DEFAULT 0"
            ))
        if ov_cols and "source" not in ov_cols:
            conn.execute(text(
                "ALTER TABLE transaction_overrides ADD COLUMN source VARCHAR(16) "
                "NOT NULL DEFAULT 'manual'"
            ))
        # Orphan NOT-NULL columns from an earlier model — new inserts fail
        # until they're dropped.
        for orphan in ("split_count", "created_at", "updated_at"):
            if orphan in ov_cols:
                conn.execute(text(
                    f"ALTER TABLE transaction_overrides DROP COLUMN {orphan}"
                ))

        user_cols = {
            row[1] for row in conn.execute(text("PRAGMA table_info(users)"))
        }
        if user_cols and "last_transactions_sync" not in user_cols:
            conn.execute(text(
                "ALTER TABLE users ADD COLUMN last_transactions_sync TIMESTAMP"
            ))
        if user_cols and "monthly_income" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN monthly_income FLOAT"))
        if user_cols and "monthly_spend" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN monthly_spend FLOAT"))

        tx_cols = {
            row[1] for row in conn.execute(text("PRAGMA table_info(transactions)"))
        }
        if tx_cols and "pfc_detailed" not in tx_cols:
            conn.execute(text(
                "ALTER TABLE transactions ADD COLUMN pfc_detailed VARCHAR(64)"
            ))
        if tx_cols and "is_internal_transfer" not in tx_cols:
            conn.execute(text(
                "ALTER TABLE transactions ADD COLUMN is_internal_transfer "
                "BOOLEAN NOT NULL DEFAULT 0"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_tx_internal_transfer "
                "ON transactions (user_id, is_internal_transfer)"
            ))
        if tx_cols and "iso_currency_code" not in tx_cols:
            conn.execute(text(
                "ALTER TABLE transactions ADD COLUMN iso_currency_code VARCHAR(3)"
            ))

        item_cols = {
            row[1] for row in conn.execute(text("PRAGMA table_info(plaid_items)"))
        }
        if item_cols and "logo" not in item_cols:
            conn.execute(text("ALTER TABLE plaid_items ADD COLUMN logo TEXT"))
        if item_cols and "institution_url" not in item_cols:
            conn.execute(text(
                "ALTER TABLE plaid_items ADD COLUMN institution_url VARCHAR(255)"
            ))
        if item_cols and "primary_color" not in item_cols:
            conn.execute(text(
                "ALTER TABLE plaid_items ADD COLUMN primary_color VARCHAR(16)"
            ))

        rate_cols = {
            row[1] for row in conn.execute(text("PRAGMA table_info(account_rates)"))
        }
        if rate_cols and "monthly_contribution" not in rate_cols:
            conn.execute(text(
                "ALTER TABLE account_rates ADD COLUMN monthly_contribution FLOAT"
            ))

        rule_cols = {
            row[1] for row in conn.execute(text("PRAGMA table_info(transaction_rules)"))
        }
        if rule_cols and "match_op" not in rule_cols:
            conn.execute(text(
                "ALTER TABLE transaction_rules ADD COLUMN match_op VARCHAR(16) "
                "NOT NULL DEFAULT 'equals'"
            ))
        if rule_cols and "scope" not in rule_cols:
            conn.execute(text(
                "ALTER TABLE transaction_rules ADD COLUMN scope VARCHAR(16) "
                "NOT NULL DEFAULT 'all'"
            ))
        if rule_cols and "conditions_logic" not in rule_cols:
            conn.execute(text(
                "ALTER TABLE transaction_rules ADD COLUMN conditions_logic VARCHAR(8) "
                "NOT NULL DEFAULT 'all'"
            ))
        if rule_cols:
            rule_indexes = {row[0] for row in conn.execute(text(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name='transaction_rules'"
            ))}
            for legacy in (
                "uq_rule_user_field_op_value_action",
                "uq_rule_user_field_op_value_action_scope",
            ):
                if legacy in rule_indexes:
                    conn.execute(text(f"DROP INDEX {legacy}"))

        # Backfill conditions for legacy rules — without rows they read as no-op.
        cond_exists = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='transaction_rule_conditions'"
        )).first()
        if rule_cols and cond_exists:
            conn.execute(text(
                "INSERT INTO transaction_rule_conditions "
                "(rule_id, match_field, match_op, match_value) "
                "SELECT id, match_field, COALESCE(match_op, 'equals'), match_value "
                "FROM transaction_rules r "
                "WHERE match_field IS NOT NULL AND match_value IS NOT NULL "
                "AND NOT EXISTS (SELECT 1 FROM transaction_rule_conditions c "
                "WHERE c.rule_id = r.id)"
            ))

        # Backfill (user_id, date) on existing DBs — create_all only seeds fresh ones.
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_tx_user_date "
            "ON transactions (user_id, date)"
        ))

        conn.commit()
    _restrict_sqlite_perms()
