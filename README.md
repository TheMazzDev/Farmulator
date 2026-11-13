# Farmulator API

The backend for Farmulator: farm management for African mixed farms. It is a modular
monolith built with Python 3.12, FastAPI, SQLAlchemy 2 (async) and PostgreSQL 16.

This first cut covers the R1 core:

- **Farm:** farm businesses (tenants), farms, a location tree (pens, houses, stores,
  shops) and enterprises (the profit centres).
- **Livestock:** species, breeds, animals with sire and dam, groups (batches, flocks),
  and an append-only event log covering weights, treatments, moves, sales and deaths.
- **Pedigree:** family trees, inbreeding coefficients, and a mating check against the
  farm's inbreeding limit.
- **Stock:** products, lots with cost, batch and expiry, and an append-only movement
  ledger. Stock is issued first-expiry-first (FEFO) and each issue is costed.

## Quick start

You need [uv](https://docs.astral.sh/uv/) and Docker (or your own PostgreSQL 16).

```bash
cp .env.example .env
docker compose up -d db                 # Postgres with the two roles already created
uv sync                                 # install dependencies
uv run alembic upgrade head             # create the tables
uv run farmulator seed-demo             # a demo pig farm; prints an X-Tenant-ID
uv run uvicorn farmulator.main:app --reload
```

Open http://localhost:8000/docs and try the endpoints. Put the id printed by
`seed-demo` in each request's `x-tenant-id` field.

If you use your own Postgres instead of Docker, run `docker/postgres/init.sql` as a
superuser first. It creates the database and the two roles.

## Everyday commands

```bash
uv run pytest                    # tests (needs Postgres, see below)
uv run ruff format src tests     # format
uv run ruff check src tests      # lint
uv run mypy                      # strict type checks
uv run alembic revision --autogenerate -m "add litters"   # new migration
```

The tests run against a real PostgreSQL database, so tenant isolation is tested at the
database level too. They create and drop their own database. Point them at a superuser
connection with `FARMULATOR_TEST_ADMIN_URL`; the default is
`postgresql://postgres:postgres@localhost:5432/postgres`, which matches the Compose
file.

## How the code is organised

```
src/farmulator/
  main.py          app factory: wires modules, error handlers, /health
  models.py        imports every module's models (for Alembic)
  cli.py           `farmulator seed-demo`
  core/            shared plumbing, no farm logic
    config.py      settings from FARMULATOR_* environment variables
    db.py          engine, sessions, and the tenant set on every transaction
    deps.py        request context (who and which tenant) and the DB session
    models.py      base class and column conventions
    errors.py      problem-details errors
    repo.py        lookups and keyset pagination
  modules/
    farm/          tenants, farms, locations, enterprises
    livestock/     species, breeds, animals, groups, events, pedigree
    stock/         products, lots, movements
migrations/        Alembic; 0001 also sets up row-level security and grants
tests/
```

Each module has the same four files:

- `models.py`: tables.
- `schemas.py`: request and response shapes.
- `service.py`: the business rules.
- `router.py`: HTTP only.

Modules talk to each other through services and models, never through another
module's router.

## Rules every module follows

**Tenant isolation is enforced by the database.** Every tenant-owned table has a
`tenant_id` and a PostgreSQL row-level security policy. Each transaction starts with
`set_config('app.tenant_id', …, true)` (see `core/db.py`), so a query can only ever see
the caller's rows, even if a bug drops a `WHERE` clause.

The API connects as `farmulator_app`, which the policies apply to. Migrations run as
`farmulator_owner`, which owns the tables and bypasses the policies. **Never point the
API at the owner or at a superuser.**

When a request body references another record (a farm, a sire, a location), the service
loads it through `get_ref`. Row-level security hides other farms' rows, so a reference
to someone else's record is refused with a 422.

**Offline-safe writes.**

- Clients may send their own UUID as `id` on creates. Stock operations take an
  `operation_id`.
- Sending the same request again returns the original result instead of creating a
  duplicate. That is what makes retries from a phone on a poor connection safe.
- Animal events and stock movements are append-only. The app role has no `UPDATE` or
  `DELETE` on them, and a correction is a new entry.
- No table can be hard-deleted by the app; records are soft-deleted with `deleted_at`.

**Stale edits are refused.** Editable records carry a `version`. A `PATCH` must send the
version it read, and a mismatch returns `409 stale_version`.

**Money and quantities are exact.** They are stored as `numeric`, never float:

- Money has 2 decimals, unit costs 4, and quantities 3.
- In JSON, these values are decimal strings (`"34.00"`), and every amount carries a
  currency.

**Errors are problem details** (`application/problem+json`) with a stable `code`, for
example:

```json
{"type": "https://docs.farmulator.app/problems/withdrawal_period", "title": "Conflict",
 "status": 409, "code": "withdrawal_period",
 "detail": "Animal G-001 is in a drug withdrawal period until 2026-11-30.",
 "withdrawal_until": "2026-11-30"}
```

**Every cost carries an enterprise.** Stock issues take an `enterprise_id`, which is how
the profit engine will charge feed and drugs to the right part of the business.

## API at a glance

| Area | Endpoints |
| --- | --- |
| Farm | `POST /v1/dev/tenants`, `GET /v1/tenant`, `/v1/farms`, `/v1/enterprises`, `/v1/locations` |
| Livestock | `/v1/species`, `/v1/breeds`, `/v1/animals` (filters, cursor paging), `/v1/animals/{id}/events`, `/v1/animal-groups`, `POST /v1/animal-events` |
| Pedigree | `GET /v1/animals/{id}/pedigree`, `GET /v1/animals/{id}/inbreeding`, `POST /v1/breeding/mating-check` |
| Stock | `/v1/products`, `POST /v1/stock/receipts`, `/issues`, `/transfers`, `/adjustments`, `GET /v1/stock/lots`, `/v1/stock/on-hand` |

Some rules worth knowing:

- A sale or slaughter during a drug withdrawal period is refused with
  `409 withdrawal_period`. A manager can override it by setting `override_withdrawal` and
  giving `override_reason`; the override is recorded on the event.
- Stock issues skip expired lots and draw from the lot that expires first.
- Transfers keep each lot's cost, batch and expiry.

## Sign-in (temporary)

For now, the caller names its farm business in the `X-Tenant-ID` header (and optionally
`X-User-ID`). This development stand-in is refused when `FARMULATOR_ENVIRONMENT` is
`production`. Phone and one-time-PIN sign-in with tokens is the next module, and
nothing below `core/deps.py` changes when it lands.

## Not done yet

- Phone sign-in, users and roles (owner, manager, worker…), and permission checks.
- Breeding protocols and scheduled tasks: sow calendars, vaccine reminders, recurring
  dipping.
- Feed recipes and mixing (the production batch), and linking treatments to stock issues.
- The offline sync endpoint ("changes since cursor"), the profit engine and licensing.
- The Docker image has not been built and tested yet; the code was tested against a
  local PostgreSQL 16.
