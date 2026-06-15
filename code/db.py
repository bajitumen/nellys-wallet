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
        if ov_cols and "rule_id" not in ov_cols:
            conn.execute(text(
                "ALTER TABLE transaction_overrides ADD COLUMN rule_id INTEGER "
                "REFERENCES transaction_rules(id) ON DELETE SET NULL"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_override_rule_id "
                "ON transaction_overrides (rule_id)"
            ))
        # Orphan NOT-NULL columns from an earlier model — new inserts fail
        # until they're dropped. DROP COLUMN requires SQLite >= 3.35.
        for orphan in ("split_count", "created_at", "updated_at"):
            if orphan in ov_cols:
                try:
                    conn.execute(text(
                        f"ALTER TABLE transaction_overrides DROP COLUMN {orphan}"
                    ))
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning(
                        "Could not DROP COLUMN %s (SQLite likely < 3.35): %s", orphan, e,
                    )

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
        if item_cols and "needs_reauth" not in item_cols:
            conn.execute(text(
                "ALTER TABLE plaid_items ADD COLUMN needs_reauth BOOLEAN NOT NULL DEFAULT 0"
            ))
        if item_cols:
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_item_user_plaid "
                "ON plaid_items (user_id, plaid_item_id)"
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

            # Drop NOT NULL on the legacy match_field/match_value columns —
            # the model treats them as orphaned/nullable but old prod schemas
            # still have the constraint, so create_rule explodes when those
            # columns aren't written. SQLite can't ALTER away NOT NULL;
            # rebuild the table.
            legacy_not_null = {
                row[1]: row[3]
                for row in conn.execute(text("PRAGMA table_info(transaction_rules)"))
            }
            if legacy_not_null.get("match_field") or legacy_not_null.get("match_value"):
                # FK enforcement MUST be off — DROP TABLE under FK=ON fires an
                # implicit DELETE that CASCADE-deletes every condition row.
                # We can't toggle the pragma inside a transaction, so refuse
                # to proceed if it's on; the operator needs to set it OFF.
                fk_state = conn.execute(text("PRAGMA foreign_keys")).scalar()
                if fk_state:
                    raise RuntimeError(
                        "Refusing to rebuild transaction_rules: PRAGMA "
                        "foreign_keys is ON. DROP TABLE under FK enforcement "
                        "would cascade-delete every rule condition. Set "
                        "foreign_keys=OFF in db._sqlite_pragmas and retry."
                    )
                # Capture every index on the old table so the rebuild doesn't
                # silently drop one we don't know about.
                preserved_indexes = [
                    (name, sql) for (name, sql) in conn.execute(text(
                        "SELECT name, sql FROM sqlite_master "
                        "WHERE type='index' AND tbl_name='transaction_rules' "
                        "AND sql IS NOT NULL"
                    ))
                ]
                # Drop any leftover temp from a prior failed attempt.
                conn.execute(text("DROP TABLE IF EXISTS transaction_rules_new"))
                conn.execute(text(
                    "CREATE TABLE transaction_rules_new ("
                    "id INTEGER PRIMARY KEY, "
                    "user_id INTEGER NOT NULL REFERENCES users(id), "
                    "match_field VARCHAR(32), "
                    "match_op VARCHAR(16) NOT NULL DEFAULT 'equals', "
                    "match_value VARCHAR(256), "
                    "action VARCHAR(32) NOT NULL, "
                    "action_value VARCHAR(64), "
                    "scope VARCHAR(16) NOT NULL DEFAULT 'all', "
                    "conditions_logic VARCHAR(8) NOT NULL DEFAULT 'all', "
                    "created_at TIMESTAMP"
                    ")"
                ))
                conn.execute(text(
                    "INSERT INTO transaction_rules_new "
                    "(id, user_id, match_field, match_op, match_value, action, "
                    "action_value, scope, conditions_logic, created_at) "
                    "SELECT id, user_id, match_field, "
                    "COALESCE(match_op, 'equals'), match_value, action, "
                    "action_value, COALESCE(scope, 'all'), "
                    "COALESCE(conditions_logic, 'all'), created_at "
                    "FROM transaction_rules"
                ))
                conn.execute(text("DROP TABLE transaction_rules"))
                conn.execute(text(
                    "ALTER TABLE transaction_rules_new RENAME TO transaction_rules"
                ))
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_transaction_rules_user_id "
                    "ON transaction_rules (user_id)"
                ))
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_transaction_rules_match_value "
                    "ON transaction_rules (match_value)"
                ))
                # Replay any other indexes that existed on the old table —
                # legacy uniqueness indexes already got dropped above, so
                # only model-defined indexes survive in `preserved_indexes`.
                for name, idx_sql in preserved_indexes:
                    if name in {
                        "ix_transaction_rules_user_id",
                        "ix_transaction_rules_match_value",
                        "uq_rule_user_field_op_value_action",
                        "uq_rule_user_field_op_value_action_scope",
                    }:
                        continue
                    conn.execute(text(idx_sql))
                # Detect corruption from THIS rebuild only — scope the check
                # to transaction_rules so pre-existing FK orphans elsewhere
                # (e.g. an orphaned transactions.item_id) don't block init_db.
                violations = list(conn.execute(text(
                    "PRAGMA foreign_key_check(transaction_rules)"
                )))
                if violations:
                    raise RuntimeError(
                        f"transaction_rules rebuild left FK violations: {violations!r}"
                    )

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

        # Drop redundant single-column indexes: composite covers them, and each
        # unused index adds write cost on every sync.
        tx_indexes = {row[0] for row in conn.execute(text(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND tbl_name='transactions'"
        ))}
        for legacy in (
            "ix_transactions_date",
            "ix_transactions_plaid_transaction_id",
            "ix_transactions_is_internal_transfer",
        ):
            if legacy in tx_indexes:
                conn.execute(text(f"DROP INDEX {legacy}"))

        conn.commit()
    _restrict_sqlite_perms()
