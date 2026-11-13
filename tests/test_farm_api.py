import uuid
from typing import Any

import httpx


async def test_health(client: httpx.AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_new_tenant_gets_default_species(farm_setup: dict[str, Any]) -> None:
    codes = {s["code"] for s in farm_setup["species"]}
    assert codes == {"pig", "cattle", "goat", "sheep", "chicken"}
    assert farm_setup["pig"]["gestation_days"] == 114


async def test_requests_without_tenant_are_refused(client: httpx.AsyncClient) -> None:
    resp = await client.get("/v1/farms")
    assert resp.status_code == 401
    assert resp.headers["content-type"] == "application/problem+json"


async def test_create_farm_is_idempotent_with_client_id(
    client: httpx.AsyncClient, tenant: dict[str, str]
) -> None:
    farm_id = str(uuid.uuid4())
    body = {"id": farm_id, "name": "Offline farm"}
    first = await client.post("/v1/farms", json=body, headers=tenant)
    again = await client.post("/v1/farms", json=body, headers=tenant)
    assert first.status_code == again.status_code == 201
    assert first.json()["id"] == again.json()["id"] == farm_id
    assert len((await client.get("/v1/farms", headers=tenant)).json()) == 1


async def test_stale_farm_update_is_refused(
    client: httpx.AsyncClient, farm_setup: dict[str, Any]
) -> None:
    h, farm = farm_setup["headers"], farm_setup["farm"]
    ok = await client.patch(
        f"/v1/farms/{farm['id']}", json={"name": "Renamed", "version": 1}, headers=h
    )
    assert ok.status_code == 200
    assert ok.json()["version"] == 2
    stale = await client.patch(
        f"/v1/farms/{farm['id']}", json={"name": "Old copy", "version": 1}, headers=h
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "stale_version"


async def test_location_parent_must_be_on_same_farm(
    client: httpx.AsyncClient, farm_setup: dict[str, Any]
) -> None:
    h = farm_setup["headers"]
    other = (await client.post("/v1/farms", json={"name": "Second farm"}, headers=h)).json()
    resp = await client.post(
        "/v1/locations",
        json={
            "farm_id": other["id"],
            "parent_id": farm_setup["house"]["id"],
            "kind": "pen",
            "name": "Pen 1",
        },
        headers=h,
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "parent_other_farm"


async def test_validation_errors_name_the_field(
    client: httpx.AsyncClient, tenant: dict[str, str]
) -> None:
    resp = await client.post(
        "/v1/enterprises", json={"name": "X", "kind": "fishing"}, headers=tenant
    )
    assert resp.status_code == 422
    assert resp.json()["errors"][0]["field"] == "kind"
