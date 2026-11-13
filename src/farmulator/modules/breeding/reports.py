"""Breeding reports: what is coming, how litters did, and whether tasks were done on time."""

import uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from farmulator.modules.breeding.models import BreedingCycle, CycleStatus, Litter
from farmulator.modules.livestock.models import Animal
from farmulator.modules.tasks.models import ScheduledTask, TaskAction, TaskStatus

ONE_DP = Decimal("0.1")


def _pct(part: int, whole: int) -> Decimal | None:
    if whole == 0:
        return None
    return (Decimal(100) * part / whole).quantize(ONE_DP, rounding=ROUND_HALF_UP)


class FarrowingDue(BaseModel):
    cycle_id: uuid.UUID
    dam_id: uuid.UUID
    dam_tag: str
    location_id: uuid.UUID | None
    service_date: date
    expected_birth_date: date
    pregnancy_confirmed: bool
    days_to_go: int


async def farrowings_due(
    session: AsyncSession, from_date: date, to_date: date, today: date, farm_id: uuid.UUID | None
) -> list[FarrowingDue]:
    stmt = (
        select(BreedingCycle, Animal.tag, Animal.location_id)
        .join(Animal, Animal.id == BreedingCycle.dam_id)
        .where(
            BreedingCycle.status.in_([CycleStatus.SERVED, CycleStatus.PREGNANT]),
            BreedingCycle.expected_birth_date.between(from_date, to_date),
        )
        .order_by(BreedingCycle.expected_birth_date, Animal.tag)
    )
    if farm_id is not None:
        stmt = stmt.where(BreedingCycle.farm_id == farm_id)
    return [
        FarrowingDue(
            cycle_id=cycle.id,
            dam_id=cycle.dam_id,
            dam_tag=tag,
            location_id=location_id,
            service_date=cycle.service_date,
            expected_birth_date=cycle.expected_birth_date,
            pregnancy_confirmed=cycle.status == CycleStatus.PREGNANT,
            days_to_go=(cycle.expected_birth_date - today).days,
        )
        for cycle, tag, location_id in (await session.execute(stmt)).all()
    ]


class LitterRow(BaseModel):
    litter_id: uuid.UUID
    cycle_id: uuid.UUID
    dam_id: uuid.UUID
    dam_tag: str
    birth_date: date
    born_alive: int
    born_dead: int
    mummified: int
    weaned_count: int | None
    pre_weaning_mortality_pct: Decimal | None


class LitterSummary(BaseModel):
    litters: int
    born_alive_per_litter: Decimal | None
    born_dead_total: int
    weaned_litters: int
    weaned_per_litter: Decimal | None
    pre_weaning_mortality_pct: Decimal | None


class LitterReport(BaseModel):
    from_date: date
    to_date: date
    summary: LitterSummary
    litters: list[LitterRow]


async def litter_performance(
    session: AsyncSession, from_date: date, to_date: date, farm_id: uuid.UUID | None
) -> LitterReport:
    stmt = (
        select(Litter, Animal.tag)
        .join(Animal, Animal.id == Litter.dam_id)
        .where(Litter.birth_date.between(from_date, to_date))
        .order_by(Litter.birth_date, Animal.tag)
    )
    if farm_id is not None:
        stmt = stmt.join(BreedingCycle, BreedingCycle.id == Litter.cycle_id).where(
            BreedingCycle.farm_id == farm_id
        )
    rows = (await session.execute(stmt)).all()
    litters = [
        LitterRow(
            litter_id=litter.id,
            cycle_id=litter.cycle_id,
            dam_id=litter.dam_id,
            dam_tag=tag,
            birth_date=litter.birth_date,
            born_alive=litter.born_alive,
            born_dead=litter.born_dead,
            mummified=litter.mummified,
            weaned_count=litter.weaned_count,
            pre_weaning_mortality_pct=(
                _pct(litter.born_alive - litter.weaned_count, litter.born_alive)
                if litter.weaned_count is not None
                else None
            ),
        )
        for litter, tag in rows
    ]
    weaned = [r for r in litters if r.weaned_count is not None]
    born_alive = sum(r.born_alive for r in litters)
    weaned_born_alive = sum(r.born_alive for r in weaned)
    weaned_total = sum(r.weaned_count or 0 for r in weaned)

    def per_litter(total: int, count: int) -> Decimal | None:
        return (Decimal(total) / count).quantize(ONE_DP) if count else None

    return LitterReport(
        from_date=from_date,
        to_date=to_date,
        summary=LitterSummary(
            litters=len(litters),
            born_alive_per_litter=per_litter(born_alive, len(litters)),
            born_dead_total=sum(r.born_dead for r in litters),
            weaned_litters=len(weaned),
            weaned_per_litter=per_litter(weaned_total, len(weaned)),
            pre_weaning_mortality_pct=_pct(weaned_born_alive - weaned_total, weaned_born_alive),
        ),
        litters=litters,
    )


class ComplianceLine(BaseModel):
    action: TaskAction
    due: int
    done_on_time: int
    done_late: int
    skipped: int
    overdue: int
    on_time_pct: Decimal | None


class ComplianceReport(BaseModel):
    from_date: date
    to_date: date
    as_of: date
    total: ComplianceLine | None
    by_action: list[ComplianceLine]


async def task_compliance(
    session: AsyncSession,
    from_date: date,
    to_date: date,
    today: date,
    farm_id: uuid.UUID | None,
) -> ComplianceReport:
    """Tasks that fell due in the period and were not cancelled. Tasks still within
    their window are left out: they can still be done on time."""
    done_on = func.date(func.timezone("UTC", ScheduledTask.done_at))
    stmt = select(
        ScheduledTask.action,
        ScheduledTask.status,
        ScheduledTask.window_end,
        done_on,
    ).where(
        ScheduledTask.due_date.between(from_date, to_date),
        ScheduledTask.status != TaskStatus.CANCELLED,
    )
    if farm_id is not None:
        stmt = stmt.where(ScheduledTask.farm_id == farm_id)

    counts: dict[TaskAction, dict[str, int]] = {}
    for action, status, window_end, done_day in (await session.execute(stmt)).all():
        if status == TaskStatus.DUE and window_end >= today:
            continue
        c = counts.setdefault(
            action, {"due": 0, "done_on_time": 0, "done_late": 0, "skipped": 0, "overdue": 0}
        )
        c["due"] += 1
        if status == TaskStatus.DONE:
            c["done_on_time" if done_day <= window_end else "done_late"] += 1
        elif status == TaskStatus.SKIPPED:
            c["skipped"] += 1
        else:
            c["overdue"] += 1

    def line(action: TaskAction, c: dict[str, int]) -> ComplianceLine:
        return ComplianceLine(action=action, on_time_pct=_pct(c["done_on_time"], c["due"]), **c)

    by_action = [line(a, c) for a, c in sorted(counts.items(), key=lambda kv: kv[0].value)]
    total = None
    if counts:
        keys = ("due", "done_on_time", "done_late", "skipped", "overdue")
        sums = {k: sum(c[k] for c in counts.values()) for k in keys}
        total = ComplianceLine(
            action=TaskAction.OTHER, on_time_pct=_pct(sums["done_on_time"], sums["due"]), **sums
        )
    return ComplianceReport(
        from_date=from_date, to_date=to_date, as_of=today, total=total, by_action=by_action
    )
