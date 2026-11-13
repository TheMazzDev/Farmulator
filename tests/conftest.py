"""Tests run against a real PostgreSQL database, so row-level security is tested too.

Set FARMULATOR_TEST_ADMIN_URL to a superuser connection (default:
postgresql://postgres:postgres@localhost:5432/postgres). The suite creates its own
database and roles, runs the migrations, and drops the database afterwards.
"""

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest

from farmulator.core.config import Settings
from farmulator.main import create_app

ROOT = Path(__file__).resolve().parent.parent
ADMIN_URL = os.environ.get(
    "FARMULATOR_TEST_ADMIN_URL", "postgresql://postgres:postgres@localhost:5432/postgres"
)
DB = "farmulator_test"
OWNER = ("farmulator_test_owner", "owner_pw")
APP = ("farmulator_test_app", "app_pw")


@dataclass(frozen=True)
class DatabaseInfo:
    owner_dsn: str
    app_dsn: str
    settings: Settings


def _dsn(role: tuple[str, str], driver: str = "postgresql") -> str:
    host = ADMIN_URL.split("@", 1)[1].rsplit("/", 1)[0]
    return f"{driver}://{role[0]}:{role[1]}@{host}/{DB}"


async def _reset_database() -> None:
    admin = await asyncpg.connect(ADMIN_URL)
    try:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1", DB
        )
        await admin.execute(f"DROP DATABASE IF EXISTS {DB}")
        for role, password in (APP, OWNER):
            await admin.execute(f"DROP ROLE IF EXISTS {role}")
            await admin.execute(f"CREATE ROLE {role} LOGIN PASSWORD '{password}'")
        await admin.execute(f"CREATE DATABASE {DB} OWNER {OWNER[0]}")
    finally:
        await admin.close()


async def _drop_database() -> None:
    admin = await asyncpg.connect(ADMIN_URL)
    try:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1", DB
        )
        await admin.execute(f"DROP DATABASE IF EXISTS {DB}")
    finally:
        await admin.close()


@pytest.fixture(scope="session")
def database() -> Iterator[DatabaseInfo]:
    asyncio.run(_reset_database())
    owner_url = _dsn(OWNER, "postgresql+asyncpg")
    env = {
        **os.environ,
        "FARMULATOR_MIGRATION_DATABASE_URL": owner_url,
        "FARMULATOR_APP_DB_ROLE": APP[0],
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    settings = Settings(
        environment="test",
        database_url=_dsn(APP, "postgresql+asyncpg"),
        migration_database_url=owner_url,
        app_db_role=APP[0],
        dev_auth_enabled=True,
        db_pool_size=2,
    )
    yield DatabaseInfo(owner_dsn=_dsn(OWNER), app_dsn=_dsn(APP), settings=settings)
    asyncio.run(_drop_database())


@pytest.fixture(autouse=True)
async def clean_tables(database: DatabaseInfo) -> None:
    conn = await asyncpg.connect(database.owner_dsn)
    try:
        # Every tenant-owned table references tenant, so this empties them all.
        await conn.execute("TRUNCATE tenant CASCADE")
    finally:
        await conn.close()


@pytest.fixture
async def client(database: DatabaseInfo) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(database.settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    await app.state.engine.dispose()


@pytest.fixture
async def owner_db(database: DatabaseInfo) -> AsyncIterator[asyncpg.Connection]:
    """A table-owner connection for checking the database directly (bypasses RLS)."""
    conn = await asyncpg.connect(database.owner_dsn)
    yield conn
    await conn.close()


Headers = dict[str, str]


@pytest.fixture
def make_tenant(client: httpx.AsyncClient) -> Callable[..., Awaitable[Headers]]:
    async def _make(name: str = "Test Farm Ltd") -> Headers:
        resp = await client.post(
            "/v1/dev/tenants", json={"name": name, "country": "ZW", "base_currency": "USD"}
        )
        assert resp.status_code == 201, resp.text
        return {"X-Tenant-ID": resp.json()["id"], "X-User-ID": str(uuid.uuid4())}

    return _make


@pytest.fixture
async def tenant(make_tenant: Callable[..., Awaitable[Headers]]) -> Headers:
    return await make_tenant()


@pytest.fixture
async def farm_setup(client: httpx.AsyncClient, tenant: Headers) -> dict[str, Any]:
    """A pig farm with a house, a feed store, an enterprise and the pig species id."""

    async def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
        resp = await client.post(path, json=body, headers=tenant)
        assert resp.status_code == 201, resp.text
        return dict(resp.json())

    farm = await post("/v1/farms", {"name": "Mazowe Farm"})
    sows = await post("/v1/enterprises", {"name": "Breeding sows", "kind": "livestock"})
    house = await post(
        "/v1/locations",
        {"farm_id": farm["id"], "kind": "pig_house", "name": "Farrowing house"},
    )
    store = await post(
        "/v1/locations", {"farm_id": farm["id"], "kind": "feed_store", "name": "Feed store"}
    )
    species = (await client.get("/v1/species", headers=tenant)).json()
    pig = next(s for s in species if s["code"] == "pig")
    return {
        "headers": tenant,
        "farm": farm,
        "enterprise": sows,
        "house": house,
        "store": store,
        "pig": pig,
        "species": species,
    }
