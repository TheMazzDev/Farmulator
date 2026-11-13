"""The sow breeding calendar: protocols, cycles, litters, tasks and reports."""

from datetime import date, timedelta
from typing import Any

import httpx
import pytest

SERVICE = date(2026, 1, 5)
EXPECTED = SERVICE + timedelta(days=114)  # pig gestation


def d(day: date, days: int = 0) -> str:
    return (day + timedelta(days=days)).isoformat()


def noon(day: date, days: int = 0) -> str:
    return f"{d(day, days)}T12:00:00+02:00"


class Piggery:
    def __init__(self, client: httpx.AsyncClient, setup: dict[str, Any]) -> None:
        self.client = client
        self.setup = setup
        self.h = setup["headers"]

    async def call(self, method: str, path: str, expect: int = 200, **kw: Any) -> Any:
        resp = await self.client.request(method, path, headers=self.h, **kw)
        assert resp.status_code == expect, resp.text
        return resp.json()

    async def animal(self, tag: str, sex: str) -> dict[str, Any]:
        body = {
            "farm_id": self.setup["farm"]["id"],
            "species_id": self.setup["pig"]["id"],
            "tag": tag,
            "sex": sex,
            "location_id": self.setup["house"]["id"],
            "enterprise_id": self.setup["enterprise"]["id"],
        }
        return dict(await self.call("POST", "/v1/animals", 201, json=body))

    async def serve(self, sow: dict[str, Any], boar: dict[str, Any], day: date = SERVICE) -> Any:
        body = {"dam_id": sow["id"], "sire_id": boar["id"], "service_date": d(day)}
        return await self.call("POST", "/v1/breeding/services", 201, json=body)

    async def tasks(self, **params: Any) -> list[dict[str, Any]]:
        return list(await self.call("GET", "/v1/tasks", params=params))

    async def protocol(self) -> dict[str, Any]:
        return dict((await self.call("GET", "/v1/protocols"))[0])


@pytest.fixture
async def pigs(client: httpx.AsyncClient, farm_setup: dict[str, Any]) -> Piggery:
    return Piggery(client, farm_setup)


def by_title(tasks: list[dict[str, Any]], words: str) -> dict[str, Any]:
    return next(t for t in tasks if words in t["title"])


async def test_new_farm_gets_example_sow_protocol(pigs: Piggery) -> None:
    protocol = await pigs.protocol()
    assert protocol["is_default"] is True
    assert protocol["approved_by_vet"] is False
    assert len(protocol["steps"]) == 12
    assert protocol["steps"][0]["code"] == "pregnancy-check"
    assert protocol["steps"][-1]["code"] == "sow-heat-check"


async def test_service_schedules_the_sows_tasks(pigs: Piggery) -> None:
    sow, boar = await pigs.animal("S-001", "female"), await pigs.animal("B-001", "male")
    out = await pigs.serve(sow, boar)
    cycle = out["cycle"]
    assert cycle["status"] == "served"
    assert cycle["expected_birth_date"] == d(EXPECTED)
    assert out["mating_check"]["offspring_inbreeding"] == "0.000000"

    tasks = {t["title"]: t for t in cycle["tasks"]}
    assert len(tasks) == 6  # litter and weaning tasks come later
    check = tasks["S-001: Pregnancy check (scan)"]
    assert (check["due_date"], check["window_end"], check["reminder_on"]) == (
        d(SERVICE, 28),
        d(SERVICE, 35),
        d(SERVICE, 26),
    )
    assert tasks["S-001: Pre-farrowing vaccine, first dose"]["due_date"] == d(EXPECTED, -35)
    assert tasks["S-001: Wash sow and move to farrowing pen"]["due_date"] == d(EXPECTED, -7)
    farrowing = tasks["S-001: Farrowing due: record the litter"]
    assert (farrowing["due_date"], farrowing["window_end"]) == (d(EXPECTED, -2), d(EXPECTED, 3))


async def test_service_rules(pigs: Piggery) -> None:
    sow, boar = await pigs.animal("S-001", "female"), await pigs.animal("B-001", "male")
    await pigs.serve(sow, boar)
    again = await pigs.call(
        "POST",
        "/v1/breeding/services",
        409,
        json={"dam_id": sow["id"], "sire_id": boar["id"], "service_date": d(SERVICE, 1)},
    )
    assert again["code"] == "open_cycle"
    male = await pigs.call(
        "POST",
        "/v1/breeding/services",
        422,
        json={"dam_id": boar["id"], "sire_id": boar["id"], "service_date": d(SERVICE)},
    )
    assert male["code"] == "dam_wrong_sex"
    sow2 = await pigs.animal("S-002", "female")
    no_sire = await pigs.call(
        "POST",
        "/v1/breeding/services",
        422,
        json={"dam_id": sow2["id"], "service_date": d(SERVICE)},
    )
    assert no_sire["code"] == "sire_required"
    ai = await pigs.call(
        "POST",
        "/v1/breeding/services",
        201,
        json={
            "dam_id": sow2["id"],
            "method": "ai",
            "straw_ref": "LW-2291",
            "service_date": d(SERVICE),
        },
    )
    assert ai["mating_check"] is None


async def test_not_pregnant_closes_the_cycle(pigs: Piggery) -> None:
    sow, boar = await pigs.animal("S-001", "female"), await pigs.animal("B-001", "male")
    cycle = (await pigs.serve(sow, boar))["cycle"]
    out = await pigs.call(
        "POST",
        f"/v1/breeding/cycles/{cycle['id']}/pregnancy-check",
        json={"checked_on": d(SERVICE, 29), "result": "not_pregnant"},
    )
    assert out["status"] == "not_pregnant"
    states = sorted(t["state"] for t in out["tasks"])
    assert states == ["cancelled"] * 5 + ["done"]
    # She can be served again.
    await pigs.serve(sow, boar, SERVICE + timedelta(days=30))


async def test_full_cycle_from_service_to_weaning(pigs: Piggery) -> None:
    sow, boar = await pigs.animal("S-001", "female"), await pigs.animal("B-001", "male")
    cycle_id = (await pigs.serve(sow, boar))["cycle"]["id"]
    base = f"/v1/breeding/cycles/{cycle_id}"

    pregnant = await pigs.call(
        "POST", f"{base}/pregnancy-check", json={"checked_on": d(SERVICE, 28), "result": "pregnant"}
    )
    assert pregnant["status"] == "pregnant"
    assert by_title(pregnant["tasks"], "Pregnancy check")["state"] == "done"

    birth_day = EXPECTED - timedelta(days=1)  # one day early
    born = await pigs.call(
        "POST",
        f"{base}/birth",
        json={"birth_date": d(birth_day), "born_alive": 12, "born_dead": 1},
    )
    assert born["status"] == "born"
    assert born["litter"]["born_alive"] == 12
    assert by_title(born["tasks"], "Farrowing due")["state"] == "done"
    iron = by_title(born["tasks"], "litter: Piglets: iron injection")
    assert iron["due_date"] == d(birth_day, 2)
    assert iron["group_id"] == born["litter_group_id"]
    assert by_title(born["tasks"], "Wean litter")["due_date"] == d(birth_day, 28)
    assert len(born["tasks"]) == 11  # 6 sow tasks + 4 litter tasks + weaning

    groups = await pigs.call("GET", "/v1/animal-groups")
    litter_group = next(g for g in groups if g["id"] == born["litter_group_id"])
    assert (litter_group["kind"], litter_group["head_count"]) == ("litter", 12)

    # One piglet dies before weaning.
    await pigs.call(
        "POST",
        "/v1/animal-events",
        201,
        json={
            "group_id": born["litter_group_id"],
            "type": "death",
            "occurred_at": noon(birth_day, 3),
            "payload": {"cause": "Crushed by sow", "count": 1},
        },
    )
    too_many = await pigs.call(
        "POST", f"{base}/weaning", 422, json={"weaning_date": d(birth_day, 26), "weaned_count": 12}
    )
    assert too_many["code"] == "count_too_high"
    weaned = await pigs.call(
        "POST",
        f"{base}/weaning",
        json={"weaning_date": d(birth_day, 26), "weaned_count": 11, "total_weight_kg": "77.0"},
    )
    assert weaned["status"] == "weaned"
    assert by_title(weaned["tasks"], "Wean litter")["state"] == "done"
    assert by_title(weaned["tasks"], "Check sow for heat")["due_date"] == d(birth_day, 30)

    report = await pigs.call(
        "GET",
        "/v1/reports/breeding/litters",
        params={"from_date": d(birth_day, -10), "to_date": d(birth_day, 10)},
    )
    assert report["summary"]["litters"] == 1
    assert report["summary"]["pre_weaning_mortality_pct"] == "8.3"  # 1 of 12
    assert report["litters"][0]["dam_tag"] == "S-001"


async def test_completing_a_vaccination_records_it_and_uses_stock(
    pigs: Piggery, client: httpx.AsyncClient
) -> None:
    store = pigs.setup["store"]["id"]
    vaccine = await pigs.call(
        "POST",
        "/v1/products",
        201,
        json={
            "sku": "VAC-ECOLI",
            "name": "E. coli sow vaccine",
            "category": "vaccine",
            "base_unit": "ml",
        },
    )
    await pigs.call(
        "POST",
        "/v1/stock/receipts",
        201,
        json={
            "product_id": vaccine["id"],
            "location_id": store,
            "quantity": "100",
            "unit_cost": "0.30",
            "currency": "USD",
            "occurred_at": noon(SERVICE),
        },
    )
    # The farm sets its product and dose on the first vaccine step.
    protocol = await pigs.protocol()
    steps = [{k: v for k, v in s.items() if k != "id"} for s in protocol["steps"]]
    first = next(s for s in steps if s["code"] == "pre-farrow-vaccine-1")
    first.update(product_id=vaccine["id"], dose="2", dose_unit="ml")
    await pigs.call(
        "PUT",
        f"/v1/protocols/{protocol['id']}",
        json={"version": protocol["version"], "steps": steps, "approved_by_vet": True},
    )

    sow, boar = await pigs.animal("S-001", "female"), await pigs.animal("B-001", "male")
    tasks = (await pigs.serve(sow, boar))["cycle"]["tasks"]
    task = by_title(tasks, "vaccine, first dose")
    assert (task["product_id"], task["dose"]) == (vaccine["id"], "2.000")

    body = {"done_at": noon(EXPECTED, -35), "quantity_used": "2", "stock_location_id": store}
    done = await pigs.call("POST", f"/v1/tasks/{task['id']}/complete", json=body)
    again = await pigs.call("POST", f"/v1/tasks/{task['id']}/complete", json=body)
    assert done["state"] == again["state"] == "done"
    assert done["done_event_id"] and done["stock_operation_id"]

    on_hand = await pigs.call("GET", "/v1/stock/on-hand")
    assert on_hand[0]["quantity"] == "98.000"  # taken once, not twice
    events = await pigs.call("GET", f"/v1/animals/{sow['id']}/events")
    vaccination = events["items"][0]
    assert vaccination["type"] == "vaccination"
    assert vaccination["payload"]["product_name"] == "E. coli sow vaccine"

    check = by_title(tasks, "Pregnancy check")
    refused = await pigs.call(
        "POST", f"/v1/tasks/{check['id']}/complete", 409, json={"done_at": noon(SERVICE, 28)}
    )
    assert refused["code"] == "use_breeding_cycle"
    deworm = by_title(tasks, "Deworm")
    missing = await pigs.call(
        "POST", f"/v1/tasks/{deworm['id']}/complete", 422, json={"done_at": noon(EXPECTED, -14)}
    )
    assert missing["code"] == "treatment_details_required"


async def test_editing_the_protocol_moves_open_tasks(pigs: Piggery) -> None:
    today = date.today()
    sow, boar = await pigs.animal("S-001", "female"), await pigs.animal("B-001", "male")
    cycle = (await pigs.serve(sow, boar, today))["cycle"]
    expected = date.fromisoformat(cycle["expected_birth_date"])

    protocol = await pigs.protocol()
    steps = [{k: v for k, v in s.items() if k != "id"} for s in protocol["steps"]]
    steps = [s for s in steps if s["code"] != "deworm-sow"]
    next(s for s in steps if s["code"] == "move-to-farrowing")["offset_days"] = -10
    steps.append(
        {
            "code": "sow-condition",
            "name": "Body condition score",
            "action": "check",
            "anchor": "expected_birth",
            "offset_days": -20,
            "window_days": 2,
            "target": "dam",
        }
    )
    updated = await pigs.call(
        "PUT",
        f"/v1/protocols/{protocol['id']}",
        json={"version": protocol["version"], "steps": steps},
    )
    assert updated["version"] == protocol["version"] + 1
    assert updated["approved_by_vet"] is False

    tasks = (await pigs.call("GET", f"/v1/breeding/cycles/{cycle['id']}"))["tasks"]
    assert by_title(tasks, "move to farrowing")["due_date"] == d(expected, -10)
    assert by_title(tasks, "Deworm")["state"] == "cancelled"
    assert by_title(tasks, "Body condition score")["due_date"] == d(expected, -20)

    stale = await pigs.call(
        "PUT",
        f"/v1/protocols/{protocol['id']}",
        409,
        json={"version": protocol["version"], "steps": steps},
    )
    assert stale["code"] == "stale_version"


async def test_protocol_validation(pigs: Piggery) -> None:
    bad = await pigs.call(
        "POST",
        "/v1/protocols",
        422,
        json={
            "species_id": pigs.setup["pig"]["id"],
            "name": "Bad",
            "steps": [
                {
                    "code": "x",
                    "name": "Piglet iron",
                    "action": "treat",
                    "anchor": "service",
                    "offset_days": 3,
                    "target": "litter",
                }
            ],
        },
    )
    assert "litter steps" in bad["errors"][0]["message"]


async def test_task_states_reminders_and_skipping(pigs: Piggery) -> None:
    sow, boar = await pigs.animal("S-001", "female"), await pigs.animal("B-001", "male")
    await pigs.serve(sow, boar)

    reminders = await pigs.call("GET", "/v1/tasks/reminders", params={"on": d(SERVICE, 26)})
    assert [t["title"] for t in reminders["reminders"]] == ["S-001: Pregnancy check (scan)"]
    assert reminders["overdue"] == []

    late = await pigs.tasks(state="overdue", on=d(SERVICE, 36))
    assert [t["title"] for t in late] == ["S-001: Pregnancy check (scan)"]
    upcoming = await pigs.tasks(state="due", on=d(SERVICE, 36))
    assert len(upcoming) == 5

    move = by_title(upcoming, "move to farrowing")
    short = await pigs.call("POST", f"/v1/tasks/{move['id']}/skip", 422, json={"reason": "no"})
    assert short["code"] == "validation_failed"
    skipped = await pigs.call(
        "POST", f"/v1/tasks/{move['id']}/skip", json={"reason": "Farrowing pen under repair"}
    )
    assert skipped["state"] == "skipped"
    await pigs.call(
        "POST", f"/v1/tasks/{move['id']}/skip", 409, json={"reason": "Farrowing pen under repair"}
    )


async def test_manual_tasks(pigs: Piggery) -> None:
    sow = await pigs.animal("S-001", "female")
    task = await pigs.call(
        "POST",
        "/v1/tasks",
        201,
        json={
            "farm_id": pigs.setup["farm"]["id"],
            "title": "Trim hooves",
            "due_date": d(SERVICE),
            "animal_id": sow["id"],
            "window_days": 2,
        },
    )
    assert (task["origin"], task["window_end"]) == ("manual", d(SERVICE, 2))
    done = await pigs.call(
        "POST", f"/v1/tasks/{task['id']}/complete", json={"done_at": noon(SERVICE, 1)}
    )
    assert done["state"] == "done"
    refused = await pigs.call(
        "POST",
        "/v1/tasks",
        422,
        json={
            "farm_id": pigs.setup["farm"]["id"],
            "title": "Scan",
            "action": "pregnancy_check",
            "due_date": d(SERVICE),
        },
    )
    assert refused["code"] == "invalid"


async def test_task_compliance_report(pigs: Piggery) -> None:
    sow, boar = await pigs.animal("S-001", "female"), await pigs.animal("B-001", "male")
    cycle = (await pigs.serve(sow, boar))["cycle"]
    tasks = cycle["tasks"]
    # On time: the pregnancy check, recorded within its window.
    await pigs.call(
        "POST",
        f"/v1/breeding/cycles/{cycle['id']}/pregnancy-check",
        json={"checked_on": d(SERVICE, 30), "result": "pregnant"},
    )
    # Late: the first vaccine, two days after its window closed.
    first = by_title(tasks, "vaccine, first dose")
    await pigs.call(
        "POST",
        f"/v1/tasks/{first['id']}/complete",
        json={
            "done_at": noon(EXPECTED, -30),
            "product_name": "Sow vaccine",
            "dose": "2",
            "dose_unit": "ml",
        },
    )
    # Skipped: the second vaccine. Never done: the deworming.
    second = by_title(tasks, "vaccine, second dose")
    await pigs.call(
        "POST", f"/v1/tasks/{second['id']}/skip", json={"reason": "Out of stock at supplier"}
    )

    report = await pigs.call(
        "GET",
        "/v1/reports/tasks/compliance",
        params={"from_date": d(SERVICE), "to_date": d(EXPECTED, -14), "on": d(EXPECTED, -10)},
    )
    total = report["total"]
    assert (total["due"], total["done_on_time"], total["done_late"]) == (4, 1, 1)
    assert (total["skipped"], total["overdue"]) == (1, 1)
    assert total["on_time_pct"] == "25.0"


async def test_farrowings_due_report(pigs: Piggery) -> None:
    boar = await pigs.animal("B-001", "male")
    early, late = await pigs.animal("S-001", "female"), await pigs.animal("S-002", "female")
    first = (await pigs.serve(early, boar))["cycle"]
    await pigs.serve(late, boar, SERVICE + timedelta(days=30))
    await pigs.call(
        "POST",
        f"/v1/breeding/cycles/{first['id']}/pregnancy-check",
        json={"checked_on": d(SERVICE, 28), "result": "pregnant"},
    )
    rows = await pigs.call(
        "GET",
        "/v1/reports/breeding/farrowings-due",
        params={"on": d(EXPECTED, -3), "from_date": d(EXPECTED, -3), "to_date": d(EXPECTED, 10)},
    )
    assert [(r["dam_tag"], r["pregnancy_confirmed"], r["days_to_go"]) for r in rows] == [
        ("S-001", True, 3)
    ]


async def test_early_birth_moves_steps_planned_from_the_due_day(pigs: Piggery) -> None:
    protocol = await pigs.call(
        "POST",
        "/v1/protocols",
        201,
        json={
            "species_id": pigs.setup["pig"]["id"],
            "name": "Short sow protocol",
            "is_default": True,
            "steps": [
                {
                    "code": "farrowing",
                    "name": "Farrowing due",
                    "action": "birth",
                    "anchor": "expected_birth",
                    "offset_days": -2,
                    "window_days": 5,
                },
                {
                    "code": "udder-check",
                    "name": "Check udder and milk",
                    "action": "check",
                    "anchor": "expected_birth",
                    "offset_days": 1,
                },
                {
                    "code": "pre-move",
                    "name": "Move to farrowing pen",
                    "action": "move",
                    "anchor": "expected_birth",
                    "offset_days": -7,
                },
            ],
        },
    )
    assert protocol["is_default"] is True
    old_default = next(
        p for p in await pigs.call("GET", "/v1/protocols") if p["id"] != protocol["id"]
    )
    assert old_default["is_default"] is False

    sow, boar = await pigs.animal("S-001", "female"), await pigs.animal("B-001", "male")
    cycle = (await pigs.serve(sow, boar))["cycle"]
    assert cycle["template_id"] == protocol["id"]
    early = EXPECTED - timedelta(days=4)
    born = await pigs.call(
        "POST",
        f"/v1/breeding/cycles/{cycle['id']}/birth",
        json={"birth_date": d(early), "born_alive": 0, "born_dead": 2},
    )
    assert by_title(born["tasks"], "udder")["due_date"] == d(early, 1)  # moved
    assert by_title(born["tasks"], "Move to")["due_date"] == d(EXPECTED, -7)  # unchanged
