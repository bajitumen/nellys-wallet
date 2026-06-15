"""Migration tests — exercise db.init_db() against pre-migration schemas."""

import sqlalchemy
from sqlalchemy import text


def test_init_db_is_idempotent():
    import db
    db.init_db()
    db.init_db()


def test_init_db_adds_iso_currency_code_to_existing_transactions():
    import db
    from db import Base, engine
    Base.metadata.drop_all(engine)
    with engine.connect() as conn:
        conn.execute(text(
            "CREATE TABLE transactions (id INTEGER PRIMARY KEY, user_id INTEGER, "
            "item_id INTEGER, plaid_transaction_id VARCHAR(64), date DATE, "
            "amount FLOAT, name VARCHAR(256), merchant_name VARCHAR(256), "
            "pfc_primary VARCHAR(64), fetched_at TIMESTAMP, "
            "created_at TIMESTAMP, updated_at TIMESTAMP)"
        ))
        conn.commit()
    db.init_db()
    with engine.connect() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(transactions)"))}
    assert "iso_currency_code" in cols
    assert "is_internal_transfer" in cols
    assert "pfc_detailed" in cols


def test_init_db_creates_unique_index_on_plaid_item_id():
    import db
    db.init_db()
    with db.engine.connect() as conn:
        names = {row[0] for row in conn.execute(text(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND tbl_name='plaid_items'"
        ))}
    assert "uq_item_user_plaid" in names


def test_init_db_drops_redundant_single_column_tx_indexes():
    import db
    from db import Base, engine
    Base.metadata.drop_all(engine)
    with engine.connect() as conn:
        conn.execute(text(
            "CREATE TABLE transactions (id INTEGER PRIMARY KEY, user_id INTEGER, "
            "item_id INTEGER, plaid_transaction_id VARCHAR(64), date DATE, "
            "amount FLOAT, name VARCHAR(256), fetched_at TIMESTAMP, "
            "created_at TIMESTAMP, updated_at TIMESTAMP)"
        ))
        conn.execute(text("CREATE INDEX ix_transactions_date ON transactions(date)"))
        conn.execute(text(
            "CREATE INDEX ix_transactions_plaid_transaction_id "
            "ON transactions(plaid_transaction_id)"
        ))
        conn.commit()
    db.init_db()
    with db.engine.connect() as conn:
        names = {row[0] for row in conn.execute(text(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND tbl_name='transactions'"
        ))}
    assert "ix_transactions_date" not in names
    assert "ix_transactions_plaid_transaction_id" not in names
    assert "ix_tx_user_date" in names


def test_init_db_backfills_legacy_rule_conditions():
    import db
    from db import Base, engine
    Base.metadata.drop_all(engine)
    db.init_db()
    with engine.connect() as conn:
        conn.execute(text(
            "INSERT INTO transaction_rules "
            "(id, user_id, match_field, match_op, match_value, action, action_value, "
            "scope, conditions_logic, created_at) VALUES "
            "(1, 1, 'merchant_name', 'equals', 'Acme', 'set_category', 'FOOD_AND_DRINK', "
            "'all', 'all', CURRENT_TIMESTAMP)"
        ))
        conn.execute(text("DELETE FROM transaction_rule_conditions WHERE rule_id=1"))
        conn.commit()
    db.init_db()
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT match_field, match_op, match_value FROM transaction_rule_conditions "
            "WHERE rule_id=1"
        )).fetchall()
    assert len(rows) == 1
    assert rows[0] == ("merchant_name", "equals", "Acme")
