import uuid
from typing import Any

import httpx

NOW = "2026-11-13T08:00:00+02:00"


class Farm:
    """Small helper around the farm_setup fixture."""

    def __init__(self, client: httpx.AsyncClient, setup: dict[str, Any]) -> None:
        self.client = client
        self.setup = setup
        self.h = setup["headers"]

    async def animal(self, tag: str, sex: str, **extra: Any) -> dict[str, Any]:
        body = {
            "farm_id": self.setup["farm"]["id"],
            "species_id": self.setup["pig"]["id"],
            "tag": tag,
            "sex": sex,
            **extra,
        }
        resp = await self.client.post("/v1/animals", json=body, headers=self.h)
        assert resp.status_code == 201, resp.text
        return dict(resp.json())

    async def event(self, body: dict[str, Any]) -> httpx.Response:
        return await self.client.post(
            "/v1/animal-events", json={"occurred_at": NOW, **body}, headers=self.h
        )


async def test_create_and_list_animals(
    client: httpx.AsyncClient, farm_setup: dict[str, Any]
) -> None:
    f = Farm(client, farm_setup)
    for n in range(3):
        await f.animal(f"S-{n:03}", "female")
    first = (await client.get("/v1/animals?limit=2", headers=f.h)).json()
    assert len(first["items"]) == 2 and first["next_cursor"]
    rest = (
        await client.get(f"/v1/animals?limit=2&cursor={first['next_cursor']}", headers=f.h)
    ).json()
    assert len(rest["items"]) == 1 and rest["next_cursor"] is None
    tagged = (await client.get("/v1/animals?tag=S-001", headers=f.h)).json()
    assert [a["tag"] for a in tagged["items"]] == ["S-001"]


async def test_duplicate_tag_is_refused(
    client: httpx.AsyncClient, farm_setup: dict[str, Any]
) -> None:
    f = Farm(client, farm_setup)
    await f.animal("S-001", "female")
    resp = await client.post(
        "/v1/animals",
        json={
            "farm_id": farm_setup["farm"]["id"],
            "species_id": farm_setup["pig"]["id"],
            "tag": "S-001",
            "sex": "female",
        },
        headers=f.h,
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "duplicate_tag"


async def test_parent_rules(client: httpx.AsyncClient, farm_setup: dict[str, Any]) -> None:
    f = Farm(client, farm_setup)
    sow = await f.animal("S-001", "female", birth_date="2024-01-01")
    boar = await f.animal("B-001", "male", birth_date="2024-01-01")
    goats = next(s for s in farm_setup["species"] if s["code"] == "goat")
    buck = await client.post(
        "/v1/animals",
        json={
            "farm_id": farm_setup["farm"]["id"],
            "species_id": goats["id"],
            "tag": "G-1",
            "sex": "male",
        },
        headers=f.h,
    )

    body = {
        "farm_id": farm_setup["farm"]["id"],
        "species_id": farm_setup["pig"]["id"],
        "sex": "female",
        "birth_date": "2025-05-01",
    }
    wrong_sex = await client.post(
        "/v1/animals", json={**body, "tag": "P-1", "sire_id": sow["id"]}, headers=f.h
    )
    assert wrong_sex.json()["code"] == "sire_wrong_sex"
    wrong_species = await client.post(
        "/v1/animals", json={**body, "tag": "P-2", "sire_id": buck.json()["id"]}, headers=f.h
    )
    assert wrong_species.json()["code"] == "sire_wrong_species"
    too_young = await client.post(
        "/v1/animals",
        json={**body, "tag": "P-3", "dam_id": sow["id"], "birth_date": "2023-01-01"},
        headers=f.h,
    )
    assert too_young.json()["code"] == "dam_too_young"
    ok = await f.animal("P-4", "female", sire_id=boar["id"], dam_id=sow["id"])
    assert ok["sire_id"] == boar["id"]


async def test_parent_cannot_be_own_offspring(
    client: httpx.AsyncClient, farm_setup: dict[str, Any]
) -> None:
    f = Farm(client, farm_setup)
    boar = await f.animal("B-001", "male")
    sow = await f.animal("S-001", "female")
    son = await f.animal("B-002", "male", sire_id=boar["id"], dam_id=sow["id"])
    resp = await client.patch(
        f"/v1/animals/{boar['id']}", json={"version": 1, "sire_id": son["id"]}, headers=f.h
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "pedigree_cycle"


async def test_weight_and_movement_events(
    client: httpx.AsyncClient, farm_setup: dict[str, Any]
) -> None:
    f = Farm(client, farm_setup)
    sow = await f.animal("S-001", "female")
    weight = await f.event(
        {"animal_id": sow["id"], "type": "weight", "payload": {"weight_kg": "182.5"}}
    )
    assert weight.status_code == 201, weight.text
    assert weight.json()["payload"]["weight_kg"] == "182.5"

    moved = await f.event(
        {
            "animal_id": sow["id"],
            "type": "movement",
            "payload": {"to_location_id": farm_setup["house"]["id"]},
        }
    )
    assert moved.status_code == 201
    animal = (await client.get(f"/v1/animals/{sow['id']}", headers=f.h)).json()
    assert animal["location_id"] == farm_setup["house"]["id"]

    events = (await client.get(f"/v1/animals/{sow['id']}/events", headers=f.h)).json()
    assert [e["type"] for e in events["items"]] == ["weight", "movement"]


async def test_event_is_idempotent_with_client_id(
    client: httpx.AsyncClient, farm_setup: dict[str, Any]
) -> None:
    f = Farm(client, farm_setup)
    sow = await f.animal("S-001", "female")
    body = {
        "id": str(uuid.uuid4()),
        "animal_id": sow["id"],
        "type": "death",
        "payload": {"cause": "Crushed"},
    }
    first, again = await f.event(body), await f.event(body)
    assert first.status_code == again.status_code == 201
    assert first.json()["id"] == again.json()["id"]
    animal = (await client.get(f"/v1/animals/{sow['id']}", headers=f.h)).json()
    assert animal["status"] == "dead"
    second_death = await f.event({**body, "id": str(uuid.uuid4())})
    assert second_death.status_code == 409


async def test_event_needs_timezone_and_one_subject(
    client: httpx.AsyncClient, farm_setup: dict[str, Any]
) -> None:
    f = Farm(client, farm_setup)
    sow = await f.animal("S-001", "female")
    no_tz = await f.event(
        {
            "animal_id": sow["id"],
            "type": "note",
            "payload": {"text": "Lame"},
            "occurred_at": "2026-11-13T08:00:00",
        }
    )
    assert no_tz.status_code == 422
    no_subject = await f.event({"type": "note", "payload": {"text": "Lame"}})
    assert no_subject.status_code == 422


async def test_withdrawal_period_blocks_sale_until_overridden(
    client: httpx.AsyncClient, farm_setup: dict[str, Any]
) -> None:
    f = Farm(client, farm_setup)
    pig = await f.animal("G-001", "castrated")
    treated = await f.event(
        {
            "animal_id": pig["id"],
            "type": "treatment",
            "payload": {
                "product_name": "Oxytetracycline LA",
                "dose": "5",
                "dose_unit": "ml",
                "withdrawal_until": "2026-11-30",
            },
        }
    )
    assert treated.status_code == 201

    sale = {
        "animal_id": pig["id"],
        "type": "sale",
        "payload": {"price": "180.00", "currency": "USD"},
    }
    blocked = await f.event(sale)
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "withdrawal_period"
    assert blocked.json()["withdrawal_until"] == "2026-11-30"

    no_reason = await f.event({**sale, "override_withdrawal": True})
    assert no_reason.status_code == 422

    allowed = await f.event(
        {**sale, "override_withdrawal": True, "override_reason": "Vet confirmed residue test"}
    )
    assert allowed.status_code == 201
    assert allowed.json()["payload"]["withdrawal_override"]["until"] == "2026-11-30"

    after = await f.event({**sale, "occurred_at": "2026-12-01T08:00:00+02:00"})
    assert after.json()["code"] == "animal_not_active"


async def test_group_deaths_reduce_head_count(
    client: httpx.AsyncClient, farm_setup: dict[str, Any]
) -> None:
    f = Farm(client, farm_setup)
    chicken = next(s for s in farm_setup["species"] if s["code"] == "chicken")
    group = (
        await client.post(
            "/v1/animal-groups",
            json={
                "farm_id": farm_setup["farm"]["id"],
                "species_id": chicken["id"],
                "name": "Broilers B12",
                "kind": "batch",
                "head_count": 500,
                "start_date": "2026-11-01",
            },
            headers=f.h,
        )
    ).json()
    died = await f.event(
        {"group_id": group["id"], "type": "death", "payload": {"cause": "Heat stress", "count": 7}}
    )
    assert died.status_code == 201
    groups = (await client.get("/v1/animal-groups", headers=f.h)).json()
    assert groups[0]["head_count"] == 493
    too_many = await f.event(
        {"group_id": group["id"], "type": "death", "payload": {"cause": "x", "count": 600}}
    )
    assert too_many.json()["code"] == "count_too_high"


async def test_pedigree_inbreeding_and_mating_check(
    client: httpx.AsyncClient, farm_setup: dict[str, Any]
) -> None:
    f = Farm(client, farm_setup)
    boar = await f.animal("B-001", "male", birth_date="2022-01-01")
    sow = await f.animal("S-001", "female", birth_date="2022-01-01")
    brother = await f.animal(
        "B-101", "male", birth_date="2023-06-01", sire_id=boar["id"], dam_id=sow["id"]
    )
    sister = await f.animal(
        "S-101", "female", birth_date="2023-06-01", sire_id=boar["id"], dam_id=sow["id"]
    )
    inbred = await f.animal(
        "S-201", "female", birth_date="2024-10-01", sire_id=brother["id"], dam_id=sister["id"]
    )
    outsider = await f.animal("B-900", "male", birth_date="2022-01-01")

    tree = (await client.get(f"/v1/animals/{inbred['id']}/pedigree", headers=f.h)).json()
    assert tree["sire"]["tag"] == "B-101"
    assert tree["sire"]["sire"]["tag"] == "B-001"
    assert tree["dam"]["dam"]["tag"] == "S-001"

    coefficient = (await client.get(f"/v1/animals/{inbred['id']}/inbreeding", headers=f.h)).json()
    assert coefficient["inbreeding_coefficient"] == "0.250000"

    risky = (
        await client.post(
            "/v1/breeding/mating-check",
            json={"sire_id": brother["id"], "dam_id": sister["id"]},
            headers=f.h,
        )
    ).json()
    assert risky["offspring_inbreeding"] == "0.250000"
    assert risky["exceeds_limit"] is True
    assert set(risky["common_ancestor_ids"]) == {boar["id"], sow["id"]}

    safe = (
        await client.post(
            "/v1/breeding/mating-check",
            json={"sire_id": outsider["id"], "dam_id": sister["id"]},
            headers=f.h,
        )
    ).json()
    assert safe["offspring_inbreeding"] == "0.000000"
    assert safe["exceeds_limit"] is False

    shallow = (
        await client.post(
            "/v1/breeding/mating-check",
            json={"sire_id": brother["id"], "dam_id": sister["id"], "generations": 1},
            headers=f.h,
        )
    ).json()
    assert shallow["offspring_inbreeding"] == "0.000000"  # grandparents not looked at
