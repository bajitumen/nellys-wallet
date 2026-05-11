"""Operational scripts. Run with: python code/cli.py <command>

Commands:
  init-db       Create all tables (idempotent).
  seed-me       Create a placeholder User and migrate permissions.env into it.
                Used once during the migration from the single-user prototype.
                Replace the placeholder later when you sign up via Clerk.
  show          Print all users and their linked items (with masked tokens).
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


COMMANDS = {"init-db": cmd_init_db, "seed-me": cmd_seed_me, "show": cmd_show}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    COMMANDS[sys.argv[1]]()
