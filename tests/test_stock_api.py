import uuid
from decimal import Decimal
from typing import Any

import asyncpg
import httpx
import pytest

from tests.conftest import DatabaseInfo

NOW = "2026-11-13T08:00:00+02:00"


async def _product(client: httpx.AsyncClient, h: dict[str, str], **extra: Any) -> dict[str, Any]:
    body = {
        "sku": "VAC-PCV2",
        "name": "Circovirus vaccine",
        "category": "vaccine",
        "base_unit": "dose",
        **extra,
    }
    resp = await client.post("/v1/products", json=body, headers=h)
    assert resp.status_code == 201, resp.text
    return dict(resp.json())


async def _receive(
    client: httpx.AsyncClient, h: dict[str, str], product: str, location: str, **extra: Any
) -> dict[str, Any]:
    body = {
        "product_id": product,
        "location_id": location,
        "currency": "USD",
        "occurred_at": NOW,
        **extra,
    }
    resp = await client.post("/v1/stock/receipts", json=body, headers=h)
    assert resp.status_code == 201, resp.text
    return dict(resp.json())


async def _ledger_matches_lots(owner_db: asyncpg.Connection) -> None:
    mismatched = await owner_db.fetch(
        """
        SELECT l.id FROM stock_lot l
        LEFT JOIN stock_movement m ON m.lot_id = l.id
        GROUP BY l.id, l.quantity_on_hand
        HAVING l.quantity_on_hand <> coalesce(sum(m.quantity), 0)
        """
    )
    assert mismatched == []


async def test_issue_takes_first_expiring_lot_and_costs_it(
    client: httpx.AsyncClient, farm_setup: dict[str, Any], owner_db: asyncpg.Connection
) -> None:
    h, store = farm_setup["headers"], farm_setup["store"]["id"]
    vaccine = await _product(client, h)
    # Received first, but expires later.
    late = await _receive(
        client,
        h,
        vaccine["id"],
        store,
        quantity="100",
        unit_cost="0.50",
        batch_number="A1",
        expiry_date="2027-06-30",
    )
    early = await _receive(
        client,
        h,
        vaccine["id"],
        store,
        quantity="30",
        unit_cost="0.80",
        batch_number="B2",
        expiry_date="2027-01-31",
        occurred_at="2026-11-13T09:00:00+02:00",
    )

    resp = await client.post(
        "/v1/stock/issues",
        json={
            "product_id": vaccine["id"],
            "location_id": store,
            "quantity": "50",
            "reason": "issue_treatment",
            "enterprise_id": farm_setup["enterprise"]["id"],
            "occurred_at": NOW,
        },
        headers=h,
    )
    assert resp.status_code == 201, resp.text
    out = resp.json()
    assert [(a["lot_id"], a["quantity"]) for a in out["allocations"]] == [
        (early["lot"]["id"], "30.000"),
        (late["lot"]["id"], "20.000"),
    ]
    # 30 x 0.80 + 20 x 0.50 = 34.00
    assert out["costs"] == [{"currency": "USD", "amount": "34.00"}]

    on_hand = (await client.get("/v1/stock/on-hand", headers=h)).json()
    assert on_hand[0]["quantity"] == "80.000"
    assert on_hand[0]["value"] == "40.00"
    await _ledger_matches_lots(owner_db)


async def test_insufficient_and_expired_stock(
    client: httpx.AsyncClient, farm_setup: dict[str, Any]
) -> None:
    h, store = farm_setup["headers"], farm_setup["store"]["id"]
    vaccine = await _product(client, h)
    await _receive(
        client, h, vaccine["id"], store, quantity="10", unit_cost="1", expiry_date="2026-10-31"
    )  # already expired on NOW
    await _receive(client, h, vaccine["id"], store, quantity="5", unit_cost="1")
    resp = await client.post(
        "/v1/stock/issues",
        json={
            "product_id": vaccine["id"],
            "location_id": store,
            "quantity": "8",
            "reason": "issue_treatment",
            "occurred_at": NOW,
        },
        headers=h,
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "insufficient_stock"
    assert Decimal(resp.json()["available"]) == 5


async def test_retried_issue_is_not_counted_twice(
    client: httpx.AsyncClient, farm_setup: dict[str, Any]
) -> None:
    h, store = farm_setup["headers"], farm_setup["store"]["id"]
    feed = await _product(
        client, h, sku="FEED-GROW", name="Grower meal", category="feed", base_unit="kg"
    )
    await _receive(client, h, feed["id"], store, quantity="1000", unit_cost="0.40")
    body = {
        "operation_id": str(uuid.uuid4()),
        "product_id": feed["id"],
        "location_id": store,
        "quantity": "250",
        "reason": "issue_feeding",
        "occurred_at": NOW,
    }
    first = await client.post("/v1/stock/issues", json=body, headers=h)
    again = await client.post("/v1/stock/issues", json=body, headers=h)
    assert first.status_code == again.status_code == 201
    assert first.json() == again.json()
    on_hand = (await client.get("/v1/stock/on-hand", headers=h)).json()
    assert on_hand[0]["quantity"] == "750.000"


async def test_transfer_keeps_cost_batch_and_expiry(
    client: httpx.AsyncClient, farm_setup: dict[str, Any], owner_db: asyncpg.Connection
) -> None:
    h, store, house = farm_setup["headers"], farm_setup["store"]["id"], farm_setup["house"]["id"]
    vaccine = await _product(client, h)
    await _receive(
        client,
        h,
        vaccine["id"],
        store,
        quantity="50",
        unit_cost="0.75",
        batch_number="C3",
        expiry_date="2027-03-31",
    )
    resp = await client.post(
        "/v1/stock/transfers",
        json={
            "product_id": vaccine["id"],
            "from_location_id": store,
            "to_location_id": house,
            "quantity": "20",
            "occurred_at": NOW,
        },
        headers=h,
    )
    assert resp.status_code == 201, resp.text
    lots = (await client.get(f"/v1/stock/lots?location_id={house}", headers=h)).json()
    assert len(lots) == 1
    assert lots[0]["unit_cost"] == "0.7500"
    assert lots[0]["batch_number"] == "C3"
    assert lots[0]["expiry_date"] == "2027-03-31"
    assert lots[0]["origin_type"] == "transfer"
    same_place = await client.post(
        "/v1/stock/transfers",
        json={
            "product_id": vaccine["id"],
            "from_location_id": store,
            "to_location_id": store,
            "quantity": "1",
            "occurred_at": NOW,
        },
        headers=h,
    )
    assert same_place.json()["code"] == "same_location"
    await _ledger_matches_lots(owner_db)


async def test_adjustment_cannot_go_below_zero(
    client: httpx.AsyncClient, farm_setup: dict[str, Any], owner_db: asyncpg.Connection
) -> None:
    h, store = farm_setup["headers"], farm_setup["store"]["id"]
    feed = await _product(
        client,
        h,
        sku="FEED-SOW",
        name="Sow meal",
        category="feed",
        base_unit="kg",
        reorder_level="500",
    )
    lot = (await _receive(client, h, feed["id"], store, quantity="600", unit_cost="0.42"))["lot"]
    counted = await client.post(
        "/v1/stock/adjustments",
        json={
            "lot_id": lot["id"],
            "quantity_delta": "-150",
            "note": "Stock count: rats",
            "occurred_at": NOW,
        },
        headers=h,
    )
    assert counted.status_code == 201
    assert counted.json()["quantity_on_hand"] == "450.000"
    on_hand = (await client.get("/v1/stock/on-hand", headers=h)).json()
    assert on_hand[0]["below_reorder_level"] is True

    too_far = await client.post(
        "/v1/stock/adjustments",
        json={"lot_id": lot["id"], "quantity_delta": "-1000", "note": "Oops", "occurred_at": NOW},
        headers=h,
    )
    assert too_far.json()["code"] == "adjustment_below_zero"
    await _ledger_matches_lots(owner_db)


async def test_ledger_is_append_only_for_the_app(
    client: httpx.AsyncClient, farm_setup: dict[str, Any], database: DatabaseInfo
) -> None:
    h, store = farm_setup["headers"], farm_setup["store"]["id"]
    feed = await _product(client, h, sku="F1", name="Feed", category="feed", base_unit="kg")
    await _receive(client, h, feed["id"], store, quantity="10", unit_cost="1")
    conn = await asyncpg.connect(database.app_dsn)
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", h["X-Tenant-ID"])
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute("UPDATE stock_movement SET quantity = 999")
    finally:
        await conn.close()
