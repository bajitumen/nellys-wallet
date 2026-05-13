"""Operational scripts. Run with: python code/cli.py <command>

Commands:
  init-db                 Create all tables (idempotent).
  seed-me                 Create a placeholder User and migrate permissions.env into it.
                          Used once during the migration from the single-user prototype.
                          Replace the placeholder later when you sign up via Clerk.
  show                    Print all users and their linked items (with masked tokens).
  backfill-institutions   Fill in PlaidItem.institution_name for any item missing it.
                          Idempotent — items that already have a name are skipped.
  sync                    Pull the last 90 days of transactions from Plaid into the
                          local DB. THIS USES PAID PLAID CREDITS — run sparingly.
  reset-items             Delete every PlaidItem, Transaction, and TransactionOverride
                          for all users. Use when switching Plaid teams — the existing
                          items hold access tokens issued by the old team and won't
                          work with the new team's credentials. Re-link via the + button
                          after running.
"""

import os
import sys

from db import SessionLocal, init_db
from models import PlaidItem, User

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PERMISSIONS_ENV = os.path.join(BASE_DIR, "permissions.env")
PLACEHOLDER_CLERK_ID = "placeholder-pre-clerk-user"
PLACEHOLDER_EMAIL = "you@local"


def cmd_init_db():
    init_db()
    print("DB initialized.")


def cmd_seed_me():
    """Migrate the single-user permissions.env into a placeholder User row.
    Idempotent: skips creation if the placeholder already exists."""
    if not os.path.exists(PERMISSIONS_ENV):
        print("No permissions.env found; nothing to migrate.")
        return

    plaid_client_id = plaid_secret = None
    tokens = []
    with open(PERMISSIONS_ENV) as f:
        for line in f:
            line = line.strip()
            if line.startswith("PLAID_CLIENT"):
                plaid_client_id = line.split("=", 1)[1].strip()
            elif line.startswith("PLAID_SECRET"):
                plaid_secret = line.split("=", 1)[1].strip()
            elif line.startswith("PLAID_ACCESS_TOKEN"):
                tokens.append(line.split("=", 1)[1].strip())

    if not plaid_client_id or not plaid_secret:
        print("permissions.env missing PLAID_CLIENT/PLAID_SECRET. Aborting.")
        return

    init_db()
    with SessionLocal() as session:
        user = session.query(User).filter_by(clerk_user_id=PLACEHOLDER_CLERK_ID).one_or_none()
        if user is None:
            user = User(clerk_user_id=PLACEHOLDER_CLERK_ID, email=PLACEHOLDER_EMAIL)
            session.add(user)
            session.flush()
            print(f"Created placeholder user id={user.id}")
        else:
            print(f"Placeholder user already exists (id={user.id}); updating credentials.")

        user.set_plaid_credentials(plaid_client_id, plaid_secret)

        existing_tokens = {item.get_access_token() for item in user.items}
        added = 0
        for token in tokens:
            if token in existing_tokens:
                continue
            item = PlaidItem(user_id=user.id)
            item.set_access_token(token)
            session.add(item)
            added += 1

        session.commit()
        print(f"Plaid credentials set, {added} new item(s) added.")


def cmd_show():
    with SessionLocal() as session:
        users = session.query(User).all()
        if not users:
            print("(no users)")
            return
        for u in users:
            creds = u.get_plaid_credentials()
            cred_status = "✓" if creds else "✗"
            print(f"User id={u.id}  clerk_id={u.clerk_user_id}  "
                  f"email={u.email}  plaid_creds={cred_status}")
            for item in u.items:
                token = item.get_access_token()
                print(f"  - item id={item.id}  inst={item.institution_name or '?'}  "
                      f"token=...{token[-4:]}")


def cmd_backfill_institutions():
    """Fill in institution_name for any PlaidItem missing it."""
    import plaid_link
    from providers import plaid_client_for

    with SessionLocal() as session:
        missing = session.query(PlaidItem).filter(PlaidItem.institution_name.is_(None)).all()
        if not missing:
            print("No items need backfilling.")
            return

        clients: dict = {}
        updated = 0
        for item in missing:
            client = clients.get(item.user_id)
            if client is None:
                try:
                    client = plaid_client_for(item.user)
                except ValueError as e:
                    print(f"  - item id={item.id}: skipped ({e})")
                    continue
                clients[item.user_id] = client

            name = plaid_link.lookup_institution_name(client, item.get_access_token())
            if name:
                item.institution_name = name
                print(f"  - item id={item.id}: set institution_name={name!r}")
                updated += 1
            else:
                print(f"  - item id={item.id}: lookup returned no institution")

        session.commit()
        print(f"Done. {updated}/{len(missing)} item(s) updated.")


def cmd_sync():
    """Sync the last 90 days of Plaid transactions into the local DB.
    Burns paid Plaid credits — run sparingly."""
    import spending
    with SessionLocal() as session:
        for user in session.query(User).all():
            print(f"Syncing user id={user.id}...")
            result = spending.sync_transactions(user, session)
            print(f"  added={result['added']}  updated={result['updated']}  "
                  f"errors={len(result['errors'])}")
            for e in result["errors"]:
                print(f"    - {e[:160]}")


def cmd_reset_items():
    """Wipe all PlaidItems, Transactions, and TransactionOverrides. Used when
    switching Plaid teams — the old items' access tokens were issued by the
    previous team and won't work under the new team's client_id/secret."""
    from models import Transaction, TransactionOverride
    with SessionLocal() as session:
        users = session.query(User).all()
        if not users:
            print("(no users)")
            return
        for user in users:
            n_items = session.query(PlaidItem).filter_by(user_id=user.id).count()
            n_tx = session.query(Transaction).filter_by(user_id=user.id).count()
            n_ov = session.query(TransactionOverride).filter_by(user_id=user.id).count()

            session.query(TransactionOverride).filter_by(user_id=user.id).delete()
            session.query(Transaction).filter_by(user_id=user.id).delete()
            session.query(PlaidItem).filter_by(user_id=user.id).delete()
            user.last_transactions_sync = None

            print(f"User id={user.id} ({user.email}): "
                  f"deleted {n_items} items, {n_tx} transactions, {n_ov} overrides")
        session.commit()
        print("Done. Restart the server and click + to re-link each institution.")


COMMANDS = {
    "init-db": cmd_init_db,
    "seed-me": cmd_seed_me,
    "show": cmd_show,
    "backfill-institutions": cmd_backfill_institutions,
    "sync": cmd_sync,
    "reset-items": cmd_reset_items,
}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    COMMANDS[sys.argv[1]]()
