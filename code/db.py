import os
import stat

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

import config


def _restrict_sqlite_perms() -> None:
    # SQLite file defaults world-readable; budgets/snapshots/overrides leak without chmod.
    if not config.DATABASE_URL.startswith("sqlite:///"):
        return
    path = config.DATABASE_URL[len("sqlite:///") :]
    if not os.path.exists(path):
        return
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
    except OSError:
        pass  # best-effort; permission errors here aren't fatal


class Base(DeclarativeBase):
    pass


engine = create_engine(
    config.DATABASE_URL,
    # SQLite-specific: allow connections from multiple threads (Flask dev server).
    connect_args={"check_same_thread": False} if config.DATABASE_URL.startswith("sqlite") else {},
    echo=False,
)


def _is_file_backed_sqlite(url: str) -> bool:
    if not url.startswith("sqlite"):
        return False
    # In-memory: sqlite:// or sqlite:///:memory:
    if url in ("sqlite://", "sqlite:///") or url.endswith(":memory:"):
        return False
    return True


if config.DATABASE_URL.startswith("sqlite"):
    # WAL allows concurrent reads with one writer (default is full DB lock).
    # busy_timeout makes writers wait up to 10s for a lock instead of failing
    # immediately — prevents user-visible hangs when a background sync overlaps
    # with a rule save.
    #
    # Operational caveats for deploys:
    #   * WAL writes two sidecar files (<db>-wal, <db>-shm). A backup that
    #     copies only the main .db file can lose committed data. Use
    #     `sqlite3.backup()` / `VACUUM INTO`, not `cp`.
    #   * WAL relies on POSIX fcntl locks; misbehaves on networked filesystems
    #     (NFS, SMB). Keep the DB on local disk.
    # WAL is skipped for in-memory test DBs (no-op there anyway).
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
        # Orphan NOT-NULL columns from an earlier model. Inserts of new
        # override rows fail until they're dropped, since the model doesn't
        # set them and the DB has no defaults.
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
        if user_cols and "count_transfers_as_transactions" not in user_cols:
            conn.execute(text(
                "ALTER TABLE users ADD COLUMN count_transfers_as_transactions "
                "BOOLEAN NOT NULL DEFAULT 1"
            ))

        tx_cols = {
            row[1] for row in conn.execute(text("PRAGMA table_info(transactions)"))
        }
        if tx_cols and "pfc_detailed" not in tx_cols:
            conn.execute(text(
                "ALTER TABLE transactions ADD COLUMN pfc_detailed VARCHAR(64)"
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
            # The old uniqueness shape is meaningless once rules can have multiple
            # conditions; drop both legacy variants.
            for legacy in (
                "uq_rule_user_field_op_value_action",
                "uq_rule_user_field_op_value_action_scope",
            ):
                if legacy in rule_indexes:
                    conn.execute(text(f"DROP INDEX {legacy}"))

        # Migrate single-condition legacy rules into the conditions table.
        cond_exists = conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='transaction_rule_conditions'"
        )).first()
        if rule_cols and cond_exists:
            already_migrated = conn.execute(text(
                "SELECT COUNT(1) FROM transaction_rule_conditions"
            )).scalar()
            if not already_migrated:
                conn.execute(text(
                    "INSERT INTO transaction_rule_conditions "
                    "(rule_id, match_field, match_op, match_value) "
                    "SELECT id, match_field, "
                    "COALESCE(match_op, 'equals'), match_value "
                    "FROM transaction_rules "
                    "WHERE match_field IS NOT NULL AND match_value IS NOT NULL"
                ))

        # Composite index for (user_id, date) Transaction reads. create_all
        # only adds the index on a fresh DB; this picks up existing DBs.
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_tx_user_date "
            "ON transactions (user_id, date)"
        ))

        conn.commit()
    _restrict_sqlite_perms()
