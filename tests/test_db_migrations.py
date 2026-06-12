"""init_db() must be idempotent across reboots.

Production boots `python code/cli.py init-db` from entrypoint.sh before
gunicorn workers start. If a re-run on an already-migrated DB ever fails
(e.g. an ALTER TABLE that doesn't guard on column-already-present), a
container restart on Render breaks the app.
"""

import sqlite3

from sqlalchemy import text


def test_init_db_is_idempotent():
    """Running init_db on an already-migrated DB must be a no-op, not raise."""
    import db as db_mod
    # The fresh_db fixture in conftest already populated the schema. Re-run
    # init_db twice and confirm nothing trips.
    db_mod.init_db()
    db_mod.init_db()
    db_mod.init_db()


def test_init_db_drops_legacy_override_orphan_columns(db_session):
    """Schema had orphan NOT NULL columns (split_count, created_at, updated_at)
    on transaction_overrides at one point. init_db must drop them and keep
    existing rows intact."""
    import db as db_mod

    # Add legacy columns back so we can prove init_db cleans them up.
    db_session.execute(text(
        "ALTER TABLE transaction_overrides ADD COLUMN split_count INTEGER NOT NULL DEFAULT 1"
    ))
    db_session.execute(text(
        "ALTER TABLE transaction_overrides ADD COLUMN created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
    ))
    db_session.execute(text(
        "ALTER TABLE transaction_overrides ADD COLUMN updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
    ))
    db_session.commit()

    # Seed a row with the legacy schema present.
    db_session.execute(text(
        "INSERT INTO transaction_overrides "
        "(user_id, plaid_transaction_id, category_override, dismissed, source) "
        "VALUES (1, 'tx_existing', 'FOOD_AND_DRINK', 0, 'manual')"
    ))
    db_session.commit()

    db_mod.init_db()

    cols = {row[1] for row in db_session.execute(text("PRAGMA table_info(transaction_overrides)"))}
    assert "split_count" not in cols
    assert "created_at" not in cols
    assert "updated_at" not in cols

    surviving = db_session.execute(text(
        "SELECT COUNT(*) FROM transaction_overrides WHERE plaid_transaction_id='tx_existing'"
    )).scalar()
    assert surviving == 1


def test_init_db_adds_missing_columns_on_legacy_schema(tmp_path, monkeypatch):
    """A DB that predates a column should get it added by init_db, then a
    second init_db call must not retry the ALTER and trip."""
    legacy_path = tmp_path / "legacy.db"
    raw = sqlite3.connect(str(legacy_path))
    raw.executescript("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            clerk_user_id VARCHAR(64),
            email VARCHAR(255)
        );
    """)
    raw.commit()
    raw.close()

    # Point a brand-new engine at the legacy DB and run init_db against it.
    from sqlalchemy import create_engine
    import db as db_mod

    engine = create_engine(f"sqlite:///{legacy_path}")
    monkeypatch.setattr(db_mod, "engine", engine)

    db_mod.init_db()
    db_mod.init_db()  # idempotent

    raw = sqlite3.connect(str(legacy_path))
    user_cols = {row[1] for row in raw.execute("PRAGMA table_info(users)")}
    raw.close()
    # init_db's manual block adds these on existing user tables.
    assert "last_transactions_sync" in user_cols
    assert "monthly_income" in user_cols
    assert "monthly_spend" in user_cols
