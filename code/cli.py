"""Operational scripts. Run with: python code/cli.py <command>

Commands:
  init-db                 Create all tables (idempotent).
  seed-me                 Create a placeholder User and migrate permissions.env into it.
                          Used once during the migration from the single-user prototype.
                          Replace the placeholder later when you sign up via Clerk.
  claim-placeholder <id>  Re-key the placeholder User row to a real Clerk user id.
                          Local-only; never exposed via HTTP.
  show                    Print all users and their linked items (with masked tokens).
  backfill-institutions   Fill in PlaidItem.institution_name and .logo for any
                          item missing either. Idempotent — items with both set
                          are skipped.
  sync [--days N]         Pull the last N days of transactions from Plaid into
                          the local DB (default 90). Use --days 730 (~2 years)
                          once after first link to backfill history; the in-app
                          Refresh button always uses 90 to keep credits cheap.
                          THIS USES PAID PLAID CREDITS — run sparingly.
  reset-items             Delete every PlaidItem, Transaction, and TransactionOverride
                          for all users. Use when switching Plaid teams — the existing
                          items hold access tokens issued by the old team and won't
                          work with the new team's credentials. Re-link via the + button
                          after running.
  probe-logo <item_id>    Make a fresh institutions_get_by_id call for a specific
                          PlaidItem and print the raw fields Plaid returned —
                          institution_id, logo presence + length, URL, primary_color.
                          Useful when a logo is mysteriously missing from the DB.
                          Costs ~1 Plaid credit per call.
  rotate-key              Re-encrypt every Plaid token under the current FERNET_KEY
                          (set FERNET_KEY_OLD to the prior key(s) first). Break-glass
                          for key rotations.
  capture-snapshots       Write today's NetWorthSnapshot for every user. Intended
                          as a daily cron (Render Cron Job) so the chart updates
                          even on days the user doesn't open the site.
                          ~1 Plaid call per linked item per user — small.
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


def cmd_claim_placeholder():
    # Local-only by design; never exposed via HTTP so external sign-ups can't trigger it.
    if len(sys.argv) < 3:
        print("Usage: cli.py claim-placeholder <clerk_user_id>")
        sys.exit(1)
    new_clerk_id = sys.argv[2].strip()
    if not new_clerk_id:
        print("clerk_user_id must be non-empty.")
        sys.exit(1)
    init_db()
    with SessionLocal() as session:
        placeholder = (
            session.query(User)
            .filter_by(clerk_user_id=PLACEHOLDER_CLERK_ID)
            .one_or_none()
        )
        if placeholder is None:
            print("No placeholder row to claim.")
            return
        existing = (
            session.query(User)
            .filter_by(clerk_user_id=new_clerk_id)
            .one_or_none()
        )
        if existing is not None:
            print(
                f"User id={existing.id} already exists for clerk_user_id={new_clerk_id}. "
                "Refusing to overwrite — merge manually."
            )
            sys.exit(1)
        placeholder.clerk_user_id = new_clerk_id
        session.commit()
        print(
            f"Claimed placeholder row id={placeholder.id} for clerk_user_id={new_clerk_id}."
        )


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
    import plaid_link
    from sqlalchemy import or_

    from providers import plaid_client_for

    with SessionLocal() as session:
        missing = session.query(PlaidItem).filter(
            or_(
                PlaidItem.institution_name.is_(None),
                PlaidItem.logo.is_(None),
                PlaidItem.institution_url.is_(None),
                PlaidItem.primary_color.is_(None),
            )
        ).all()
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

            info = plaid_link.lookup_institution(client, item.get_access_token())
            if info:
                if info.get("name") and not item.institution_name:
                    item.institution_name = info["name"]
                if info.get("logo") and not item.logo:
                    item.logo = info["logo"]
                if info.get("url") and not item.institution_url:
                    item.institution_url = info["url"]
                if info.get("primary_color") and not item.primary_color:
                    item.primary_color = info["primary_color"]
                print(
                    f"  - item id={item.id}: name={item.institution_name!r} "
                    f"logo={'set' if item.logo else 'missing'} "
                    f"color={item.primary_color or 'missing'}"
                )
                updated += 1
            else:
                print(f"  - item id={item.id}: lookup returned no institution")

        session.commit()
        print(f"Done. {updated}/{len(missing)} item(s) updated.")


def cmd_sync():
    # Burns paid Plaid credits — run sparingly.
    import spending
    days = 90
    if "--days" in sys.argv:
        try:
            days = int(sys.argv[sys.argv.index("--days") + 1])
            if days <= 0:
                raise ValueError("must be positive")
        except (IndexError, ValueError) as e:
            print(f"Bad --days value: {e}. Usage: python code/cli.py sync [--days N]")
            return
    print(f"Syncing {days} day(s) of transactions...")
    with SessionLocal() as session:
        for user in session.query(User).all():
            print(f"Syncing user id={user.id}...")
            result = spending.sync_transactions(user, session, days=days)
            print(f"  added={result['added']}  updated={result['updated']}  "
                  f"errors={len(result['errors'])}")
            for err in result["errors"]:
                print(f"    - {err[:160]}")


def cmd_reset_items():
    # Old access tokens are bound to the issuing Plaid team; switching teams needs a wipe.
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


def cmd_probe_logo():
    from plaid.model.country_code import CountryCode
    from plaid.model.institutions_get_by_id_request import InstitutionsGetByIdRequest
    from plaid.model.institutions_get_by_id_request_options import (
        InstitutionsGetByIdRequestOptions,
    )
    from plaid.model.item_get_request import ItemGetRequest

    from providers import plaid_client_for

    if len(sys.argv) < 3:
        print("Usage: python code/cli.py probe-logo <item_id>")
        return
    try:
        target_id = int(sys.argv[2])
    except ValueError:
        print(f"Bad item_id: {sys.argv[2]!r}")
        return

    with SessionLocal() as session:
        item = session.query(PlaidItem).filter_by(id=target_id).one_or_none()
        if item is None:
            print(f"No PlaidItem with id={target_id}")
            return
        print(f"PlaidItem id={item.id}  institution_name={item.institution_name!r}")
        try:
            client = plaid_client_for(item.user)
        except ValueError as e:
            print(f"Plaid client error: {e}")
            return

        item_resp = client.item_get(ItemGetRequest(access_token=item.get_access_token()))
        institution_id = getattr(item_resp.item, "institution_id", None)
        print(f"Plaid institution_id: {institution_id}")
        if not institution_id:
            print("Item has no institution_id; can't fetch institution.")
            return

        inst_resp = client.institutions_get_by_id(InstitutionsGetByIdRequest(
            institution_id=institution_id,
            country_codes=[CountryCode("US")],
            options=InstitutionsGetByIdRequestOptions(include_optional_metadata=True),
        ))
        inst = inst_resp.institution
        logo = getattr(inst, "logo", None)
        print(f"name:           {getattr(inst, 'name', None)!r}")
        print(f"url:            {getattr(inst, 'url', None)!r}")
        print(f"primary_color:  {getattr(inst, 'primary_color', None)!r}")
        print(f"logo present:   {bool(logo)}")
        print(f"logo length:    {len(logo) if logo else 0}")
        if logo:
            print(f"logo prefix:    {logo[:40]}...")
        else:
            print("→ Plaid did not return a logo for this institution_id.")


def cmd_rotate_key():
    # Re-encrypt every token under current FERNET_KEY; run after setting
    # FERNET_KEY_OLD to the prior keys, then unset FERNET_KEY_OLD.
    import crypto
    from models import PlaidItem, User
    with SessionLocal() as session:
        n_items = n_users = 0
        for item in session.query(PlaidItem).all():
            if item.access_token_encrypted:
                item.access_token_encrypted = crypto.rotate(item.access_token_encrypted)
                n_items += 1
        for user in session.query(User).all():
            if user.plaid_client_id_encrypted:
                user.plaid_client_id_encrypted = crypto.rotate(user.plaid_client_id_encrypted)
            if user.plaid_secret_encrypted:
                user.plaid_secret_encrypted = crypto.rotate(user.plaid_secret_encrypted)
            if user.plaid_client_id_encrypted or user.plaid_secret_encrypted:
                n_users += 1
        session.commit()
        print(f"Rotated {n_items} access tokens and {n_users} user credential pairs.")
        print("Once verified, unset FERNET_KEY_OLD.")


def cmd_capture_snapshots():
    # Daily cron entry point — captures today's NetWorthSnapshot for every
    # user. Run via Render Cron Job (or any scheduler) so the chart updates
    # on days the user doesn't visit. Idempotent: capture() deletes any
    # existing same-day snapshot before inserting.
    import networth
    import providers
    with SessionLocal() as session:
        users = session.query(User).all()
        ok = skipped = errored = 0
        for user in users:
            if not user.items:
                skipped += 1
                continue
            try:
                snap = networth.capture(user, session)
                if snap is None:
                    skipped += 1
                else:
                    ok += 1
            except Exception:
                errored += 1
                import logging
                logging.getLogger(__name__).exception(
                    "capture-snapshots failed for user_id=%s", user.id,
                )
            finally:
                providers.invalidate_cache(user.id)
        print(f"capture-snapshots: ok={ok} skipped={skipped} errored={errored}")


COMMANDS = {
    "init-db": cmd_init_db,
    "seed-me": cmd_seed_me,
    "claim-placeholder": cmd_claim_placeholder,
    "show": cmd_show,
    "backfill-institutions": cmd_backfill_institutions,
    "sync": cmd_sync,
    "reset-items": cmd_reset_items,
    "probe-logo": cmd_probe_logo,
    "rotate-key": cmd_rotate_key,
    "capture-snapshots": cmd_capture_snapshots,
}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    COMMANDS[sys.argv[1]]()
