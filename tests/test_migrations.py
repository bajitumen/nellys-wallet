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


def test_init_db_drops_not_null_on_legacy_rule_columns_and_create_rule_works():
    """Exercises the prod-shape failure: original schema had match_field +
    match_value as NOT NULL, but the model now treats them as nullable —
    create_rule was crashing in prod because SQLite enforced the old constraint.

    Reproduces that schema by hand, runs init_db(), then verifies a real
    create_rule against the migrated table.
    """
    import db
    from db import Base, engine, SessionLocal
    from models import User
    Base.metadata.drop_all(engine)
    # Seed users via the live schema first so the rebuild's FK_CHECK has a
    # valid target — otherwise the migration correctly refuses on a dangling FK.
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        session.add(User(clerk_user_id="seed", email="s@x"))
        session.commit()
    # Replace transaction_rules with the prod-shape NOT NULL schema and seed a row.
    with engine.connect() as conn:
        conn.execute(text("DROP TABLE transaction_rules"))
        conn.execute(text(
            "CREATE TABLE transaction_rules ("
            "id INTEGER PRIMARY KEY, "
            "user_id INTEGER NOT NULL, "
            "match_field VARCHAR(32) NOT NULL, "
            "match_op VARCHAR(16) NOT NULL DEFAULT 'equals', "
            "match_value VARCHAR(256) NOT NULL, "
            "action VARCHAR(32) NOT NULL, "
            "action_value VARCHAR(64), "
            "scope VARCHAR(16) NOT NULL DEFAULT 'all', "
            "conditions_logic VARCHAR(8) NOT NULL DEFAULT 'all', "
            "created_at TIMESTAMP"
            ")"
        ))
        conn.execute(text(
            "INSERT INTO transaction_rules "
            "(user_id, match_field, match_op, match_value, action, action_value, "
            "scope, conditions_logic, created_at) VALUES "
            "(1, 'merchant_name', 'equals', 'Acme', 'set_category', 'FOOD_AND_DRINK', "
            "'all', 'all', CURRENT_TIMESTAMP)"
        ))
        conn.commit()

    db.init_db()

    with engine.connect() as conn:
        cols = {row[1]: row[3] for row in conn.execute(text(
            "PRAGMA table_info(transaction_rules)"
        ))}
        existing_count = conn.execute(text(
            "SELECT COUNT(*) FROM transaction_rules"
        )).scalar()
    assert cols.get("match_field") == 0, "match_field should be nullable after migration"
    assert cols.get("match_value") == 0, "match_value should be nullable after migration"
    assert existing_count == 1, "pre-migration row must survive the table rebuild"

    # And now create_rule, which writes only the new shape, must succeed.
    import rules as rules_mod
    with SessionLocal() as session:
        u = session.query(User).filter_by(clerk_user_id="seed").one()
        rule = rules_mod.create_rule(
            user_id=u.id,
            conditions=[{"match_field": "merchant_name", "match_op": "equals",
                         "match_value": "Acme"}],
            conditions_logic="all", action="dismiss", action_value=None,
            scope="spending", session=session,
        )
        session.commit()
        rule_id = rule.id
    assert rule_id is not None


def test_init_db_preserves_extra_indexes_on_rebuild():
    """A custom index on transaction_rules must survive the NOT-NULL rebuild."""
    import db
    from db import Base, engine
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        conn.execute(text("DROP TABLE transaction_rules"))
        conn.execute(text(
            "CREATE TABLE transaction_rules ("
            "id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, "
            "match_field VARCHAR(32) NOT NULL, "
            "match_op VARCHAR(16) NOT NULL DEFAULT 'equals', "
            "match_value VARCHAR(256) NOT NULL, "
            "action VARCHAR(32) NOT NULL, action_value VARCHAR(64), "
            "scope VARCHAR(16) NOT NULL DEFAULT 'all', "
            "conditions_logic VARCHAR(8) NOT NULL DEFAULT 'all', "
            "created_at TIMESTAMP)"
        ))
        conn.execute(text(
            "CREATE INDEX ix_rules_custom ON transaction_rules (action, scope)"
        ))
        conn.commit()

    db.init_db()

    with engine.connect() as conn:
        names = {row[0] for row in conn.execute(text(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND tbl_name='transaction_rules'"
        ))}
    assert "ix_rules_custom" in names, "custom index lost across rebuild"
    assert "ix_transaction_rules_user_id" in names
    assert "ix_transaction_rules_match_value" in names


def test_init_db_refuses_to_rebuild_rules_when_foreign_keys_on(monkeypatch):
    """If FK enforcement is on, DROP TABLE would CASCADE-delete every rule
    condition. The migration must refuse to proceed rather than corrupt data.
    """
    import db
    from db import Base, engine
    Base.metadata.drop_all(engine)
    with engine.connect() as conn:
        conn.execute(text(
            "CREATE TABLE transaction_rules ("
            "id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, "
            "match_field VARCHAR(32) NOT NULL, "
            "match_op VARCHAR(16) NOT NULL DEFAULT 'equals', "
            "match_value VARCHAR(256) NOT NULL, "
            "action VARCHAR(32) NOT NULL, action_value VARCHAR(64), "
            "scope VARCHAR(16) NOT NULL DEFAULT 'all', "
            "conditions_logic VARCHAR(8) NOT NULL DEFAULT 'all', "
            "created_at TIMESTAMP)"
        ))
        conn.commit()

    # Stand up a connect-time listener that forces FK on for this run.
    from sqlalchemy import event
    def force_fk_on(dbapi_connection, _record):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
    event.listen(engine, "connect", force_fk_on)
    try:
        # Force a fresh connection so the listener fires.
        engine.dispose()
        import pytest
        with pytest.raises(RuntimeError, match="foreign_keys is ON"):
            db.init_db()
    finally:
        event.remove(engine, "connect", force_fk_on)
        engine.dispose()


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
