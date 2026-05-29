import os
import stat

from sqlalchemy import create_engine
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
        if rule_cols:
            rule_indexes = {row[0] for row in conn.execute(text(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name='transaction_rules'"
            ))}
            if "uq_rule_user_field_op_value_action" in rule_indexes:
                conn.execute(text("DROP INDEX uq_rule_user_field_op_value_action"))
            if "uq_rule_user_field_op_value_action_scope" not in rule_indexes:
                conn.execute(text(
                    "CREATE UNIQUE INDEX uq_rule_user_field_op_value_action_scope "
                    "ON transaction_rules "
                    "(user_id, match_field, match_op, match_value, action, scope)"
                ))

        # Composite index for (user_id, date) Transaction reads. create_all
        # only adds the index on a fresh DB; this picks up existing DBs.
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_tx_user_date "
            "ON transactions (user_id, date)"
        ))

        conn.commit()
    _restrict_sqlite_perms()
