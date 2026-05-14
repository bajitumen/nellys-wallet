# Nelly's Wallet

A small, self-hosted personal finance dashboard for me, my friends, my family,
and Nelly. It links to your bank and brokerage accounts via [Plaid](https://plaid.com),
stores tokens encrypted at rest, syncs transactions into a local SQLite database,
and renders a two-page UI:

- **Overview** — live balances grouped into Cash, Credit, Investments, and Other,
  with a derived Net total.
- **Spending** — month-by-month category breakdown, a cumulative spend chart,
  a filter by institution, and per-transaction overrides (recategorize, split
  a shared charge by percentage).

The app is currently in a **single-user prototype** phase. The data model is
already multi-user (every row is keyed by `user_id`), but identity is stubbed
out with a placeholder user seeded from a local `permissions.env`. The next
phase swaps in [Clerk](https://clerk.com) for real auth — the environment
already reserves the Clerk variables and the routes are organized to drop in
session-resolved users without restructuring.

---

## Table of contents

- [Features](#features)
- [Architecture](#architecture)
- [Project layout](#project-layout)
- [Data model](#data-model)
- [Plaid integration](#plaid-integration)
- [Security model](#security-model)
- [Caching](#caching)
- [Setup](#setup)
- [Running the app](#running-the-app)
- [Linking accounts](#linking-accounts)
- [Operational CLI](#operational-cli)
- [HTTP routes](#http-routes)
- [Environment variables](#environment-variables)
- [Database](#database)
- [Tests](#tests)
- [Troubleshooting](#troubleshooting)
- [Roadmap](#roadmap)

---

## Features

### Overview page
- Aggregates balances across every linked institution into four buckets:
  Cash (`depository`), Credit (`credit`), Investments (`investment` /
  `brokerage`), and Other (anything else, e.g. `loan`).
- Computes Net = Cash + Investments − Credit Owed.
- Renders one table per non-empty bucket with institution, account name,
  type, masked number, available balance, and current balance.
- Surfaces per-item Plaid errors inline instead of failing the whole page.

### Spending page
- Pulls one month of locally persisted transactions, applies user overrides,
  and aggregates by Plaid's [Personal Finance Category](https://plaid.com/docs/api/products/transactions/#personal-finance-category)
  primary tier (`FOOD_AND_DRINK`, `TRANSPORTATION`, …).
- **Month picker** with the last 12 months in a dropdown.
- **Source filter** (tabbed) to narrow to a single institution.
- **Cumulative chart**: inline SVG anchored at (start-of-month, $0) and
  stepping up on each transaction date. No JS chart library — the path is
  computed server-side in [code/spending.py](code/spending.py).
- **Transactions table** with a kebab menu per row offering:
  - *Recategorize* — pick a new PFC primary category.
  - *Split* — enter the percentage you actually owe (e.g. 25% of a group
    dinner); the displayed amount and category total are scaled accordingly.
  - *Reset to original* — clear all overrides for that transaction.
- Excludes `INCOME`, `TRANSFER_IN`, and `TRANSFER_OUT` from the spend total
  (configurable via `EXCLUDED_CATEGORIES` in [code/spending.py](code/spending.py)).
- Refunds / negative amounts are dropped (Plaid uses positive amounts for
  outflows).

### UI niceties
- **Dark mode** with `localStorage` persistence, defaulting to the OS
  preference. Toggle lives at the bottom of the sidebar.
- **Refresh button** (top-right) triggers `POST /sync`, busts the in-memory
  caches, and reloads. The button's `title` shows "Last synced N min ago".
- **Add account button** opens Plaid Link in a modal directly inside the app,
  no separate script needed once the first item is linked.
- Static assets get a 1-day `Cache-Control`.

---

## Architecture

A short request flow for the Spending page:

```
Browser
  │ GET /spending?month=2026-04&source=Chase
  ▼
Flask (code/app.py)
  │ @with_user opens a SQLAlchemy session and resolves the current user
  ▼
spending.fetch_last_month(user, month, source, session)
  │ checks 60s in-process cache keyed by (user_id, month, source)
  │ on miss: SELECT from transactions + transaction_overrides
  │          aggregates totals, builds SVG path, caches
  ▼
Jinja renders templates/spending.html (extends _layout.html)
```

The sync flow:

```
Browser clicks Refresh
  │ POST /sync
  ▼
spending.sync_transactions(user, session)
  │ for each PlaidItem: client.transactions_get (paginated, parallel)
  │ upsert by plaid_transaction_id
  │ stamp user.last_transactions_sync
  │ invalidate spending + provider caches
```

The only function that hits Plaid for transactions is
`spending.sync_transactions`. Page loads read from the local DB.

---

## Project layout

```
nellys-wallet/
├── README.md
├── pyproject.toml          # project metadata, deps, ruff config
├── uv.lock                 # uv-resolved dependency lockfile
├── .env.example            # template for required env vars
├── .gitignore              # excludes .env, permissions.env, *.db, *.token
│
├── code/                   # all application code lives here
│   ├── app.py              # Flask routes + bootstrap
│   ├── cli.py              # operational scripts (seed-me, sync, …)
│   ├── config.py           # .env loader, required-var validation
│   ├── crypto.py           # Fernet wrappers (encrypt/decrypt)
│   ├── db.py               # SQLAlchemy engine + lightweight SQLite migrations
│   ├── models.py           # User, PlaidItem, Transaction, TransactionOverride
│   ├── plaid_link.py       # Plaid Link helpers (create token, exchange, lookup)
│   ├── providers.py        # balance fetch + per-user caching
│   ├── spending.py         # sync_transactions + fetch_last_month + chart
│   ├── enroll_plaid.py     # standalone browser flow for first-time linking
│   ├── static/
│   │   └── favicon.svg
│   └── templates/
│       ├── _layout.html    # sidebar, theme toggle, refresh/add buttons, JS
│       ├── _macros.html    # shared Jinja macros (empty_state)
│       ├── dashboard.html  # Overview page
│       └── spending.html   # Spending page
│
├── tests/                  # pytest, uses a temp SQLite per run
│   ├── conftest.py         # fixtures: fresh_db, clear_caches, user, client
│   ├── test_providers.py   # balance fetch + caching
│   ├── test_spending.py    # aggregation, overrides, sync
│   └── test_routes.py      # Flask test-client smoke tests
│
└── instance/               # created at runtime; holds finance.db by default
```

---

## Data model

All tables are defined in [code/models.py](code/models.py). Schema is created by
`code/db.py:init_db()`, which is idempotent and also runs a couple of in-place
`ALTER TABLE` migrations for fields added after the initial schema.

### `users`
| Column | Notes |
|---|---|
| `id` | primary key |
| `clerk_user_id` | unique; placeholder value `placeholder-pre-clerk-user` until Clerk lands |
| `email` | indexed |
| `created_at` | UTC |
| `plaid_client_id_encrypted` | Fernet-encrypted bytes |
| `plaid_secret_encrypted` | Fernet-encrypted bytes |
| `last_transactions_sync` | naive UTC timestamp; powers the "Last synced X ago" indicator |

Each user supplies their own Plaid Trial credentials. There is no shared
Plaid app.

### `plaid_items`
One row per linked institution.
| Column | Notes |
|---|---|
| `id` | primary key |
| `user_id` | FK → users |
| `institution_name` | populated on link; backfillable via CLI |
| `plaid_item_id` | indexed |
| `access_token_encrypted` | Fernet-encrypted |
| `created_at` | UTC |

### `transactions`
Locally persisted copy of Plaid transactions. Populated only by
`spending.sync_transactions`.
| Column | Notes |
|---|---|
| `id` | primary key |
| `user_id`, `item_id` | FKs |
| `plaid_transaction_id` | unique per `(user_id, plaid_transaction_id)` |
| `date`, `amount`, `name`, `merchant_name` | as returned by Plaid |
| `pfc_primary` | Plaid Personal Finance Category, primary tier |
| `fetched_at`, `created_at`, `updated_at` | UTC |

### `transaction_overrides`
User adjustments layered on at read time. Plaid's data is never mutated.
| Column | Notes |
|---|---|
| `user_id`, `plaid_transaction_id` | unique together |
| `category_override` | raw PFC primary code |
| `amount_override` | scaled amount, e.g. your share of a split bill |
| `split_percentage` | metadata for the "· 25% share" badge in the table |

---

## Plaid integration

The app uses the official `plaid-python` SDK and the **Plaid Production**
environment (set in [code/providers.py](code/providers.py)). Each user brings
their own Plaid Trial credentials.

| Plaid API | Where it's used | Why |
|---|---|---|
| `link/token/create` | [code/plaid_link.py](code/plaid_link.py:21) | Generates a short-lived token for Plaid Link |
| `item/public_token/exchange` | [code/plaid_link.py](code/plaid_link.py:53) | Trades a `public_token` for the long-lived `access_token` |
| `item/get` + `institutions/get_by_id` | [code/plaid_link.py](code/plaid_link.py:34) | Resolves a friendly institution name |
| `accounts/get` | [code/providers.py](code/providers.py:55) | Cached balances (sub-second). Avoids `accounts/balance/get`, which makes a live call to the bank and adds multi-second latency per item |
| `transactions/get` | [code/spending.py](code/spending.py:82) | Pulls a 90-day window; paginates 250 at a time |

Per-item Plaid calls are fanned out with a `ThreadPoolExecutor` (max 8
workers) so adding more institutions does not linearly slow down the page.

Errors are caught and surfaced inline rather than blowing up the whole page —
e.g. an item whose token has expired will render an error message under the
account table while the rest of the page renders normally.

---

## Security model

- **All Plaid tokens** (client_id, secret, access_token) are encrypted at rest
  via [Fernet](https://cryptography.io/en/latest/fernet/) (AES-128-CBC +
  HMAC-SHA256). The key lives in `FERNET_KEY` and **never** in the database.
- The DB columns are `LargeBinary`, so the ciphertext lives in the column
  directly — no extra encoding layer.
- `permissions.env` and any `*.env` files are gitignored. The only template
  committed is `.env.example`.
- The Flask `secret_key` is a separate `FLASK_SECRET_KEY` value, also held in
  `.env`.
- The app does not currently set CSRF tokens — it is intended for local /
  trusted-LAN use until Clerk lands and a proper session model is added.

If you ever rotate `FERNET_KEY`, every encrypted column becomes unreadable.
Generate a new key with:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

## Caching

Two short-lived in-process caches keep the UI snappy:

| Cache | Where | TTL | Key | Invalidated by |
|---|---|---|---|---|
| Balances | [code/providers.py](code/providers.py:27) | 90s | `user.id` | `providers.invalidate_cache(user_id)` — called on `/sync` and `/link/exchange` |
| Spending aggregation | [code/spending.py](code/spending.py:28) | 60s | `(user_id, month, source)` | `spending.invalidate_cache(user_id)` — called on `/sync` and on every override mutation |

Both are guarded by `threading.Lock()` so the Flask dev server's threaded
mode is safe. Tests reset both caches with the `clear_caches` autouse
fixture in [tests/conftest.py](tests/conftest.py).

---

## Setup

### Prerequisites
- Python 3.11+
- A Plaid account (Trial / Sandbox / Development / Production keys)
- [`uv`](https://github.com/astral-sh/uv) recommended (the repo ships a
  `uv.lock`), but plain `pip` works too

### 1. Install dependencies

With `uv`:

```bash
uv sync
source .venv/bin/activate
```

Or with pip:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

For tests and linting:

```bash
uv sync --extra dev
# or: pip install -e ".[dev]"
```

### 2. Create `.env`

```bash
cp .env.example .env
```

Fill in:

```ini
FERNET_KEY=             # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
FLASK_SECRET_KEY=       # any long random string
DATABASE_URL=sqlite:///instance/finance.db
FLASK_ENV=development

# Reserved for the Clerk migration; leave blank for now
CLERK_PUBLISHABLE_KEY=
CLERK_SECRET_KEY=
CLERK_JWT_PUBLIC_KEY=
```

### 3. Create `permissions.env` (pre-Clerk only)

This file is read once by `python code/cli.py seed-me` and migrated into the
encrypted columns on the placeholder user. Format:

```ini
PLAID_CLIENT=...
PLAID_SECRET=...
# Optional — only if you already have access tokens from a prior link flow:
PLAID_ACCESS_TOKEN=...
PLAID_ACCESS_TOKEN=...
```

After `seed-me` runs, this file is no longer used. You can delete it.

### 4. Initialize the database and seed the placeholder user

```bash
python code/cli.py init-db
python code/cli.py seed-me
```

`init-db` is idempotent. `seed-me` is idempotent too — it updates the
placeholder user's credentials if it already exists.

---

## Running the app

```bash
python code/app.py
```

The app listens on **http://localhost:5001**. Debug mode is enabled when
`FLASK_ENV=development`.

For production-style serving (the `prod` extra installs gunicorn):

```bash
uv sync --extra prod
gunicorn -w 2 -b 0.0.0.0:5001 --chdir code app:app
```

---

## Linking accounts

There are two equivalent flows:

### A. In-app (preferred, once you have a user)

Click the **+** button in the page header. The app fetches a Link token
via `POST /link/token`, Plaid Link opens in a modal, and on success the
public token is exchanged via `POST /link/exchange`. The new item appears
on the next reload.

### B. Standalone script (useful for first-time setup / multi-institution batch)

```bash
python code/enroll_plaid.py
```

This opens **http://localhost:8766** in your browser with Plaid Link already
initialized. Each successful link calls `/exchange` and saves the item; a
"Connect Another" button repeats the flow. Ctrl+C when done.

Once at least one institution is linked, run a sync to pull transactions:

```bash
python code/cli.py sync
# or: click the Refresh button in the UI
```

> **Note**: `sync` consumes paid Plaid credits if you are not on a free
> tier — re-run sparingly.

---

## Operational CLI

All scripts live in [code/cli.py](code/cli.py) and run via
`python code/cli.py <command>`:

| Command | What it does |
|---|---|
| `init-db` | Creates all tables. Idempotent. Also applies in-place `ALTER TABLE` migrations for fields added later. |
| `seed-me` | Migrates `permissions.env` (PLAID_CLIENT, PLAID_SECRET, PLAID_ACCESS_TOKEN×N) into a placeholder user row. Idempotent. |
| `show` | Prints every user and their linked items, with the last 4 chars of each access token. |
| `backfill-institutions` | Calls `item/get` + `institutions/get_by_id` for any `PlaidItem` missing `institution_name`. Idempotent — skips items that already have a name. |
| `sync` | Pulls the last 90 days of transactions for every user. **Burns paid Plaid credits.** |
| `reset-items` | **Destructive.** Wipes every `PlaidItem`, `Transaction`, and `TransactionOverride` for every user. Use when rotating Plaid teams — old access tokens won't work under new client credentials. Re-link via the + button afterward. |

---

## HTTP routes

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Overview page. Renders empty state if no user or no linked items. |
| GET | `/spending?month=YYYY-MM&source=Name` | Spending page. Both params optional. |
| POST | `/sync` | Trigger Plaid → DB transaction sync for the current user. Returns `{ok, added, updated, errors}`. |
| POST | `/link/token` | Returns a Plaid Link token scoped to the current user. |
| POST | `/link/exchange` | Body `{public_token}`. Exchanges, persists a `PlaidItem`, returns `{item_id, institution_name}`. |
| POST | `/transactions/<tx_id>/override` | Upsert / clear a `TransactionOverride`. Body fields are all optional: `category`, `amount`, `split_percentage`, `clear`. |
| GET | `/static/*` | Static assets with `Cache-Control: public, max-age=86400`. |

All routes use the `@with_user` decorator, which opens a SQLAlchemy session,
resolves the current user (currently: first user in the DB; future: Clerk
session), and stashes the user on `flask.g` so templates can render the
header chrome consistently.

---

## Environment variables

Loaded by [code/config.py](code/config.py) via `python-dotenv`.

| Var | Required | Default | Purpose |
|---|---|---|---|
| `FERNET_KEY` | yes | — | Symmetric key for token encryption. **Rotating this breaks all existing rows.** |
| `FLASK_SECRET_KEY` | yes | — | Flask session cookie signing. |
| `DATABASE_URL` | no | `sqlite:///instance/finance.db` | SQLAlchemy URL. Defaults to a local SQLite file inside `instance/`. |
| `FLASK_ENV` | no | `development` | When `development`, Flask runs in debug mode. |
| `CLERK_PUBLISHABLE_KEY` | no | `""` | Reserved for the upcoming Clerk integration. |
| `CLERK_SECRET_KEY` | no | `""` | Reserved. |
| `CLERK_JWT_PUBLIC_KEY` | no | `""` | Reserved. |

A missing required var raises `RuntimeError: Missing required env var: …` on
import, before any web traffic is served.

---

## Database

- Default DB is **SQLite** at `instance/finance.db`. The `instance/` folder is
  created on import of [code/config.py](code/config.py).
- The engine is built with `check_same_thread=False` so the Flask dev
  server's threaded mode can share connections safely.
- [code/db.py](code/db.py:23) is the single migration entry point. It runs
  `Base.metadata.create_all(engine)` (idempotent) and then a couple of
  defensive `ALTER TABLE` statements for columns introduced after the initial
  schema (`transaction_overrides.split_percentage`,
  `users.last_transactions_sync`). This avoids needing Alembic for a
  single-machine prototype, but Alembic is already a declared dependency for
  when the schema becomes too complex to migrate inline.
- Switching to Postgres is a one-line change to `DATABASE_URL` plus adding
  `psycopg`. The inline SQLite migrations would need to be removed or guarded
  by dialect.

---

## Tests

```bash
pytest
```

The suite uses a temporary SQLite file per process (configured in
[tests/conftest.py](tests/conftest.py)) and `autouse` fixtures to:

- Drop and recreate all tables between tests (`fresh_db`).
- Clear the providers and spending caches (`clear_caches`).

Shared fixtures:
- `db_session` — a raw SQLAlchemy session.
- `user` — a User with placeholder Plaid creds.
- `user_with_item` — a User with one linked `PlaidItem`.
- `client` — a Flask test client.

Coverage breakdown:
- [tests/test_providers.py](tests/test_providers.py) — account-bucket
  classification, parallel fetch, cache hit / force-refresh / invalidate.
- [tests/test_spending.py](tests/test_spending.py) — aggregation, excluded
  categories, negative-amount handling, recategorize/split overrides, source
  filter, month resolution, cache hit/invalidate, sync upsert behavior, chart
  generation, and `relative_time` formatting.
- [tests/test_routes.py](tests/test_routes.py) — every Flask route as a smoke
  test, including the empty-state branches and the cache-invalidation
  side effects of `/link/exchange` and `/sync`.

Lint with ruff:

```bash
ruff check .
```

Ruff is configured for `E, F, I, B, UP` rules, line length 100, Python 3.11
target (see `[tool.ruff]` in [pyproject.toml](pyproject.toml)).

---

## Troubleshooting

**`Missing required env var: FERNET_KEY`** — copy `.env.example` to `.env`
and fill in `FERNET_KEY` and `FLASK_SECRET_KEY`. See [Setup](#setup).

**Refresh button shows "Sync failed: User has no Plaid credentials configured."**
The placeholder user has no encrypted Plaid client/secret yet. Run
`python code/cli.py seed-me` after creating `permissions.env`.

**"Transactions not yet ready" error after linking a new institution.**
Plaid takes a few minutes to prepare transactions on first link. Hit
Refresh again after a couple of minutes. The error is non-fatal — the
account balance still shows on Overview.

**Items still show "Unknown" as their institution.** Run
`python code/cli.py backfill-institutions`. This calls Plaid for any item
whose `institution_name` is null.

**Switching Plaid teams.** Access tokens are scoped to the Plaid team that
issued them, so they'll fail under a new team's `client_id`/`secret`. Run
`python code/cli.py reset-items` to wipe items + transactions, update
credentials in `permissions.env` and re-run `seed-me`, then re-link each
institution from the UI.

**Rotated `FERNET_KEY` by mistake.** Every encrypted column is now
undecryptable. Wipe with `reset-items` and re-link, or restore the previous
key from your secret manager. There is no recovery path inside the app.

---

## Roadmap

- **Clerk auth.** `User.clerk_user_id` is already the source of truth; the
  `current_user(session)` stub in [code/app.py](code/app.py:25) is the swap
  point. The Clerk env vars are reserved.
- **Alembic migrations.** Replace the inline SQLite `ALTER TABLE`s in
  [code/db.py](code/db.py) once the schema stops fitting on a single screen.
- **Postgres.** Trivial DSN swap; needs the inline SQLite migrations gated
  on dialect.
- **Recurring expenses / budgets.** The `transactions` table already has
  everything needed; the UI is the missing piece.
- **CSRF + auth-aware static caching.** Will come in alongside Clerk.
