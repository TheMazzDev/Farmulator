"""One farm business must never see or touch another's data, at the API or the database."""

import uuid
from collections.abc import Awaitable, Callable

import asyncpg
import httpx
import pytest

from tests.conftest import DatabaseInfo

Headers = dict[str, str]


async def test_other_tenant_cannot_see_farms(
    client: httpx.AsyncClient, make_tenant: Callable[..., Awaitable[Headers]]
) -> None:
    a, b = await make_tenant("A"), await make_tenant("B")
    farm = (await client.post("/v1/farms", json={"name": "A's farm"}, headers=a)).json()

    assert (await client.get("/v1/farms", headers=b)).json() == []
    assert (await client.get(f"/v1/farms/{farm['id']}", headers=b)).status_code == 404
    assert (await client.get("/v1/tenant", headers=b)).json()["name"] == "B"


async def test_other_tenant_cannot_reference_rows(
    client: httpx.AsyncClient, make_tenant: Callable[..., Awaitable[Headers]]
) -> None:
    a, b = await make_tenant("A"), await make_tenant("B")
    farm = (await client.post("/v1/farms", json={"name": "A's farm"}, headers=a)).json()
    resp = await client.post(
        "/v1/locations", json={"farm_id": farm["id"], "kind": "pen", "name": "Sneaky"}, headers=b
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "farm_not_found"


async def test_database_hides_rows_without_tenant_setting(
    client: httpx.AsyncClient,
    make_tenant: Callable[..., Awaitable[Headers]],
    database: DatabaseInfo,
) -> None:
    a = await make_tenant("A")
    await client.post("/v1/farms", json={"name": "A's farm"}, headers=a)

    conn = await asyncpg.connect(database.app_dsn)
    try:
        assert await conn.fetchval("SELECT count(*) FROM farm") == 0
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", a["X-Tenant-ID"])
            assert await conn.fetchval("SELECT count(*) FROM farm") == 1
        # The setting was transaction-local: gone again.
        assert await conn.fetchval("SELECT count(*) FROM farm") == 0
    finally:
        await conn.close()


async def test_database_refuses_writing_another_tenants_rows(
    make_tenant: Callable[..., Awaitable[Headers]], database: DatabaseInfo
) -> None:
    a, b = await make_tenant("A"), await make_tenant("B")
    conn = await asyncpg.connect(database.app_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", a["X-Tenant-ID"])
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(
                    "INSERT INTO farm (id, tenant_id, name, timezone, version) "
                    "VALUES ($1, $2, 'Planted', 'UTC', 1)",
                    uuid.uuid4(),
                    uuid.UUID(b["X-Tenant-ID"]),
                )
    finally:
        await conn.close()
