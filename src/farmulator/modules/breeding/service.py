import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from farmulator.core.deps import RequestContext
from farmulator.core.errors import Conflict, Invalid, NotFound
from farmulator.core.repo import get_or_404, get_ref
from farmulator.modules.breeding.models import (
    OPEN_CYCLE_STATUSES,
    BreedingCycle,
    CycleStatus,
    Litter,
    ProtocolStep,
    ProtocolTemplate,
    StepAnchor,
    StepTarget,
)
from farmulator.modules.breeding.schemas import (
    BirthIn,
    CycleOut,
    LitterOut,
    PregnancyCheckIn,
    ServiceIn,
    ServiceOut,
    StepIn,
    StepOut,
    TemplateCreate,
    TemplateOut,
    TemplateUpdate,
    WeaningIn,
)
from farmulator.modules.farm.models import Location
from farmulator.modules.livestock import service as livestock_service
from farmulator.modules.livestock.models import (
    Animal,
    AnimalGroup,
    AnimalStatus,
    GroupKind,
    Sex,
    Species,
)
from farmulator.modules.stock.models import Product
from farmulator.modules.tasks.models import ScheduledTask, TaskAction, TaskOrigin, TaskStatus
from farmulator.modules.tasks.schemas import TaskOut

ANCHOR_ORDER = {
    StepAnchor.SERVICE: 0,
    StepAnchor.EXPECTED_BIRTH: 1,
    StepAnchor.BIRTH: 2,
    StepAnchor.WEANING: 3,
}
# Cycles whose tasks still follow their protocol when the protocol is edited.
SCHEDULED_STATUSES = (*OPEN_CYCLE_STATUSES, CycleStatus.WEANED)


def _at_noon(day: date) -> datetime:
    """A record kept by date only, stored as midday UTC so it stays on the same date."""
    return datetime.combine(day, time(12, 0), tzinfo=UTC)


# --- Protocol templates -------------------------------------------------------------


async def _steps(session: AsyncSession, template_id: uuid.UUID) -> list[ProtocolStep]:
    stmt = select(ProtocolStep).where(
        ProtocolStep.template_id == template_id, ProtocolStep.is_active.is_(True)
    )
    steps = (await session.execute(stmt)).scalars().all()
    return sorted(steps, key=lambda s: (ANCHOR_ORDER[s.anchor], s.offset_days, s.code))


async def template_out(session: AsyncSession, template: ProtocolTemplate) -> TemplateOut:
    steps = await _steps(session, template.id)
    return TemplateOut(
        id=template.id,
        species_id=template.species_id,
        name=template.name,
        is_default=template.is_default,
        approved_by_vet=template.approved_by_vet,
        notes=template.notes,
        version=template.version,
        steps=[StepOut.model_validate(s) for s in steps],
    )


async def _check_step_products(session: AsyncSession, steps: list[StepIn]) -> None:
    for step in steps:
        await get_ref(session, Product, step.product_id, "product")


async def _make_only_default(session: AsyncSession, template: ProtocolTemplate) -> None:
    await session.execute(
        update(ProtocolTemplate)
        .where(
            ProtocolTemplate.species_id == template.species_id,
            ProtocolTemplate.id != template.id,
            ProtocolTemplate.is_default.is_(True),
        )
        .values(is_default=False, version=ProtocolTemplate.version + 1)
        .execution_options(synchronize_session="fetch")
    )


async def create_template(
    session: AsyncSession, ctx: RequestContext, data: TemplateCreate
) -> ProtocolTemplate:
    await get_ref(session, Species, data.species_id, "species")
    await _check_step_products(session, data.steps)
    template = ProtocolTemplate(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        species_id=data.species_id,
        name=data.name,
        is_default=data.is_default,
        approved_by_vet=data.approved_by_vet,
        notes=data.notes,
    )
    session.add(template)
    await session.flush()  # the template row must exist before its steps
    for step in data.steps:
        session.add(
            ProtocolStep(tenant_id=ctx.tenant_id, template_id=template.id, **step.model_dump())
        )
    await session.flush()
    if template.is_default:
        await _make_only_default(session, template)
    return template


async def list_templates(
    session: AsyncSession, species_id: uuid.UUID | None
) -> Sequence[ProtocolTemplate]:
    stmt = select(ProtocolTemplate).where(ProtocolTemplate.deleted_at.is_(None))
    if species_id is not None:
        stmt = stmt.where(ProtocolTemplate.species_id == species_id)
    return (await session.execute(stmt.order_by(ProtocolTemplate.name))).scalars().all()


async def update_template(
    session: AsyncSession, ctx: RequestContext, template_id: uuid.UUID, data: TemplateUpdate
) -> ProtocolTemplate:
    template = await get_or_404(session, ProtocolTemplate, template_id, "protocol")
    if template.version != data.version:
        raise Conflict(
            "This protocol was changed by someone else. Reload it and try again.",
            code="stale_version",
            current_version=template.version,
        )
    await _check_step_products(session, data.steps)
    for field in ("name", "is_default", "approved_by_vet", "notes"):
        value = getattr(data, field)
        if value is not None:
            setattr(template, field, value)
    # Edits that change what the farm should do clear the vet's approval, unless the
    # same request re-approves it.
    if data.approved_by_vet is None:
        template.approved_by_vet = False

    existing = {
        s.code: s
        for s in (
            await session.execute(
                select(ProtocolStep).where(ProtocolStep.template_id == template.id)
            )
        ).scalars()
    }
    incoming = {s.code for s in data.steps}
    for step in data.steps:
        current = existing.get(step.code)
        if current is None:
            session.add(
                ProtocolStep(tenant_id=ctx.tenant_id, template_id=template.id, **step.model_dump())
            )
        else:
            for field, value in step.model_dump().items():
                setattr(current, field, value)
            current.is_active = True
    for code, step_row in existing.items():
        if code not in incoming:
            step_row.is_active = False
    template.updated_at = datetime.now(UTC)  # bumps the version even if only steps changed
    try:
        await session.flush()
    except StaleDataError as exc:
        raise Conflict("This protocol was changed by someone else.", code="stale_version") from exc
    if template.is_default:
        await _make_only_default(session, template)

    # Bring running cycles in line with the new protocol.
    today = datetime.now(UTC).date()
    cycles = (
        await session.execute(
            select(BreedingCycle).where(
                BreedingCycle.template_id == template.id,
                BreedingCycle.status.in_(SCHEDULED_STATUSES),
            )
        )
    ).scalars()
    for cycle in cycles:
        await _schedule(session, ctx, cycle, not_before=today)
    return template


# --- Scheduling ---------------------------------------------------------------------


def _anchor_date(cycle: BreedingCycle, step: ProtocolStep) -> date | None:
    if step.anchor == StepAnchor.SERVICE:
        return cycle.service_date
    if step.anchor == StepAnchor.EXPECTED_BIRTH:
        # Once she has farrowed, steps planned for on or after the expected day follow
        # the real birth date instead.
        if cycle.birth_date is not None and step.offset_days >= 0:
            return cycle.birth_date
        return cycle.expected_birth_date
    if step.anchor == StepAnchor.BIRTH:
        return cycle.birth_date
    return cycle.weaning_date


async def _schedule(
    session: AsyncSession,
    ctx: RequestContext,
    cycle: BreedingCycle,
    *,
    not_before: date | None = None,
) -> None:
    """Make the cycle's tasks match its protocol for every date the cycle knows.

    New steps get tasks; open tasks move to the step's current timing; tasks of
    removed steps are cancelled. Done and skipped tasks are never touched.
    ``not_before`` stops a protocol edit from creating tasks that are already late.
    """
    dam = await session.get(Animal, cycle.dam_id)
    assert dam is not None
    litter = (
        await session.execute(select(Litter).where(Litter.cycle_id == cycle.id))
    ).scalar_one_or_none()
    has_piglets = litter is not None and litter.born_alive > 0

    steps = await _steps(session, cycle.template_id)
    tasks = {
        t.step_id: t
        for t in (
            await session.execute(select(ScheduledTask).where(ScheduledTask.cycle_id == cycle.id))
        ).scalars()
    }

    for step in steps:
        anchor = _anchor_date(cycle, step)
        if anchor is None:
            continue
        if step.target == StepTarget.LITTER and not has_piglets:
            continue
        due = anchor + timedelta(days=step.offset_days)
        window_end = due + timedelta(days=step.window_days)
        reminder_on = due - timedelta(days=step.reminder_days_before)
        if step.target == StepTarget.LITTER:
            title, animal_id, group_id = (
                f"{dam.tag} litter: {step.name}",
                None,
                cycle.litter_group_id,
            )
        else:
            title, animal_id, group_id = f"{dam.tag}: {step.name}", dam.id, None

        task = tasks.get(step.id)
        if task is None:
            if not_before is not None and window_end < not_before:
                continue
            session.add(
                ScheduledTask(
                    tenant_id=ctx.tenant_id,
                    farm_id=cycle.farm_id,
                    title=title,
                    action=step.action,
                    due_date=due,
                    window_end=window_end,
                    reminder_on=reminder_on,
                    origin=TaskOrigin.PROTOCOL,
                    cycle_id=cycle.id,
                    step_id=step.id,
                    animal_id=animal_id,
                    group_id=group_id,
                    product_id=step.product_id,
                    dose=step.dose,
                    dose_unit=step.dose_unit,
                    instructions=step.instructions,
                )
            )
        elif task.status == TaskStatus.DUE:
            task.title = title
            task.action = step.action
            task.due_date = due
            task.window_end = window_end
            task.reminder_on = reminder_on
            task.product_id = step.product_id
            task.dose = step.dose
            task.dose_unit = step.dose_unit
            task.instructions = step.instructions

    active = {s.id for s in steps}
    for step_id, task in tasks.items():
        if step_id not in active and task.status == TaskStatus.DUE:
            task.status = TaskStatus.CANCELLED
            task.skip_reason = "Step removed from the protocol."
    await session.flush()


async def _tick_off(
    session: AsyncSession, ctx: RequestContext, cycle: BreedingCycle, action: TaskAction, day: date
) -> None:
    stmt = select(ScheduledTask).where(
        ScheduledTask.cycle_id == cycle.id,
        ScheduledTask.action == action,
        ScheduledTask.status == TaskStatus.DUE,
    )
    for task in (await session.execute(stmt)).scalars():
        task.status = TaskStatus.DONE
        task.done_at = _at_noon(day)
        task.done_by = ctx.user_id


async def _cancel_open(session: AsyncSession, cycle: BreedingCycle, reason: str) -> None:
    stmt = select(ScheduledTask).where(
        ScheduledTask.cycle_id == cycle.id, ScheduledTask.status == TaskStatus.DUE
    )
    for task in (await session.execute(stmt)).scalars():
        task.status = TaskStatus.CANCELLED
        task.skip_reason = reason


# --- Breeding cycle -----------------------------------------------------------------


async def _locked_cycle(session: AsyncSession, cycle_id: uuid.UUID) -> BreedingCycle:
    stmt = select(BreedingCycle).where(BreedingCycle.id == cycle_id).with_for_update()
    cycle = (await session.execute(stmt)).scalar_one_or_none()
    if cycle is None:
        raise NotFound(f"No breeding cycle with id {cycle_id}.", code="breeding_cycle_not_found")
    return cycle


async def record_service(session: AsyncSession, ctx: RequestContext, data: ServiceIn) -> ServiceOut:
    if data.id is not None and (existing := await session.get(BreedingCycle, data.id)):
        return ServiceOut(cycle=await cycle_out(session, existing, _today()))

    dam = await get_ref(session, Animal, data.dam_id, "dam")
    assert dam is not None
    if dam.sex != Sex.FEMALE:
        raise Invalid("The dam must be female.", code="dam_wrong_sex")
    if dam.status != AnimalStatus.ACTIVE or dam.farm_id is None:
        raise Invalid(f"{dam.tag} is not an active animal on a farm.", code="dam_not_active")
    species = await session.get(Species, dam.species_id)
    assert species is not None
    if not species.gestation_days:
        raise Invalid(f"Set a gestation length for {species.name} first.", code="gestation_unknown")
    if data.method == "natural" and data.sire_id is None:
        raise Invalid("A natural service needs the sire.", code="sire_required")

    if data.template_id is not None:
        template = await get_ref(session, ProtocolTemplate, data.template_id, "protocol")
        assert template is not None
        if template.species_id != dam.species_id:
            raise Invalid("That protocol is for another species.", code="protocol_wrong_species")
    else:
        template = (
            await session.execute(
                select(ProtocolTemplate).where(
                    ProtocolTemplate.species_id == dam.species_id,
                    ProtocolTemplate.is_default.is_(True),
                    ProtocolTemplate.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if template is None:
            raise Invalid(
                f"No default breeding protocol for {species.name}. Create one or choose one.",
                code="no_protocol",
            )

    open_cycle = await session.scalar(
        select(BreedingCycle.id).where(
            BreedingCycle.dam_id == dam.id, BreedingCycle.status.in_(OPEN_CYCLE_STATUSES)
        )
    )
    if open_cycle is not None:
        raise Conflict(
            f"{dam.tag} already has an open breeding cycle. Record its outcome first.",
            code="open_cycle",
            cycle_id=str(open_cycle),
        )

    mating = None
    if data.sire_id is not None:
        # Checks the sire's sex and species, and how inbred the litter would be.
        mating = await livestock_service.mating_check(session, ctx, data.sire_id, dam.id, 5)

    cycle = BreedingCycle(
        id=data.id or uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        farm_id=dam.farm_id,
        dam_id=dam.id,
        sire_id=data.sire_id,
        template_id=template.id,
        method=data.method,
        straw_ref=data.straw_ref,
        service_date=data.service_date,
        expected_birth_date=data.service_date + timedelta(days=species.gestation_days),
        status=CycleStatus.SERVED,
        note=data.note,
    )
    session.add(cycle)
    await session.flush()
    await _schedule(session, ctx, cycle)
    return ServiceOut(cycle=await cycle_out(session, cycle, _today()), mating_check=mating)


async def record_pregnancy_check(
    session: AsyncSession, ctx: RequestContext, cycle_id: uuid.UUID, data: PregnancyCheckIn
) -> BreedingCycle:
    cycle = await _locked_cycle(session, cycle_id)
    if cycle.status not in (CycleStatus.SERVED, CycleStatus.PREGNANT):
        raise Conflict(
            f"This cycle is {cycle.status.value}; a pregnancy check no longer applies.",
            code="cycle_wrong_status",
        )
    if data.checked_on < cycle.service_date:
        raise Invalid("The check cannot be before the service.", code="date_before_service")
    cycle.pregnancy_checked_on = data.checked_on
    if data.note:
        cycle.note = data.note
    await _tick_off(session, ctx, cycle, TaskAction.PREGNANCY_CHECK, data.checked_on)
    if data.result == "pregnant":
        cycle.status = CycleStatus.PREGNANT
    else:
        cycle.status = CycleStatus.NOT_PREGNANT
        await _cancel_open(session, cycle, "Not pregnant.")
    await session.flush()
    return cycle


async def record_birth(
    session: AsyncSession, ctx: RequestContext, cycle_id: uuid.UUID, data: BirthIn
) -> BreedingCycle:
    cycle = await _locked_cycle(session, cycle_id)
    if cycle.status not in (CycleStatus.SERVED, CycleStatus.PREGNANT):
        raise Conflict(
            f"This cycle is {cycle.status.value}; a birth cannot be recorded.",
            code="cycle_wrong_status",
        )
    if data.birth_date <= cycle.service_date:
        raise Invalid("The birth must be after the service.", code="date_before_service")
    dam = await session.get(Animal, cycle.dam_id)
    assert dam is not None
    location = await get_ref(session, Location, data.location_id, "location")

    group = AnimalGroup(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        farm_id=cycle.farm_id,
        species_id=dam.species_id,
        breed_id=dam.breed_id,
        name=f"Litter of {dam.tag}, {data.birth_date.isoformat()}",
        kind=GroupKind.LITTER,
        head_count=data.born_alive,
        start_date=data.birth_date,
        location_id=location.id if location else dam.location_id,
        enterprise_id=dam.enterprise_id,
    )
    session.add(group)
    await session.flush()  # the group row must exist before the litter points at it
    session.add(
        Litter(
            tenant_id=ctx.tenant_id,
            cycle_id=cycle.id,
            dam_id=dam.id,
            sire_id=cycle.sire_id,
            group_id=group.id,
            birth_date=data.birth_date,
            born_alive=data.born_alive,
            born_dead=data.born_dead,
            mummified=data.mummified,
        )
    )
    cycle.birth_date = data.birth_date
    cycle.status = CycleStatus.BORN
    cycle.litter_group_id = group.id
    await session.flush()
    await _tick_off(session, ctx, cycle, TaskAction.BIRTH, data.birth_date)
    await _schedule(session, ctx, cycle)
    return cycle


async def record_weaning(
    session: AsyncSession, ctx: RequestContext, cycle_id: uuid.UUID, data: WeaningIn
) -> BreedingCycle:
    cycle = await _locked_cycle(session, cycle_id)
    if cycle.status != CycleStatus.BORN:
        raise Conflict(
            f"This cycle is {cycle.status.value}; only a litter on the dam can be weaned.",
            code="cycle_wrong_status",
        )
    assert cycle.birth_date is not None and cycle.litter_group_id is not None
    if data.weaning_date < cycle.birth_date:
        raise Invalid("Weaning cannot be before the birth.", code="date_before_birth")
    group = await session.get(AnimalGroup, cycle.litter_group_id, with_for_update=True)
    assert group is not None
    if data.weaned_count > group.head_count:
        raise Invalid(
            f"The litter has {group.head_count} piglets on record. "
            "Record any extra births or fostering first.",
            code="count_too_high",
        )
    litter = (await session.execute(select(Litter).where(Litter.cycle_id == cycle.id))).scalar_one()
    # Any difference from the head count is piglets lost before weaning.
    group.head_count = data.weaned_count
    litter.weaned_on = data.weaning_date
    litter.weaned_count = data.weaned_count
    litter.weaning_weight_kg = data.total_weight_kg
    cycle.weaning_date = data.weaning_date
    cycle.status = CycleStatus.WEANED
    await session.flush()
    await _tick_off(session, ctx, cycle, TaskAction.WEANING, data.weaning_date)
    await _schedule(session, ctx, cycle)
    return cycle


# --- Reading ------------------------------------------------------------------------


def _today() -> date:
    return datetime.now(UTC).date()


async def cycle_out(
    session: AsyncSession, cycle: BreedingCycle, today: date, *, with_tasks: bool = True
) -> CycleOut:
    litter = (
        await session.execute(select(Litter).where(Litter.cycle_id == cycle.id))
    ).scalar_one_or_none()
    tasks: list[TaskOut] = []
    if with_tasks:
        rows = (
            await session.execute(
                select(ScheduledTask)
                .where(ScheduledTask.cycle_id == cycle.id)
                .order_by(ScheduledTask.due_date, ScheduledTask.title)
            )
        ).scalars()
        tasks = [TaskOut.build(t, today) for t in rows]
    return CycleOut(
        id=cycle.id,
        farm_id=cycle.farm_id,
        dam_id=cycle.dam_id,
        sire_id=cycle.sire_id,
        template_id=cycle.template_id,
        method=cycle.method,
        straw_ref=cycle.straw_ref,
        service_date=cycle.service_date,
        expected_birth_date=cycle.expected_birth_date,
        pregnancy_checked_on=cycle.pregnancy_checked_on,
        birth_date=cycle.birth_date,
        weaning_date=cycle.weaning_date,
        status=cycle.status,
        litter_group_id=cycle.litter_group_id,
        note=cycle.note,
        version=cycle.version,
        litter=LitterOut.model_validate(litter) if litter else None,
        tasks=tasks,
    )


async def get_cycle(session: AsyncSession, cycle_id: uuid.UUID) -> BreedingCycle:
    return await get_or_404(session, BreedingCycle, cycle_id, "breeding cycle")


async def list_cycles(
    session: AsyncSession,
    *,
    farm_id: uuid.UUID | None,
    dam_id: uuid.UUID | None,
    status: CycleStatus | None,
) -> Sequence[BreedingCycle]:
    stmt = select(BreedingCycle)
    if farm_id is not None:
        stmt = stmt.where(BreedingCycle.farm_id == farm_id)
    if dam_id is not None:
        stmt = stmt.where(BreedingCycle.dam_id == dam_id)
    if status is not None:
        stmt = stmt.where(BreedingCycle.status == status)
    stmt = stmt.order_by(BreedingCycle.service_date.desc(), BreedingCycle.id).limit(500)
    return (await session.execute(stmt)).scalars().all()
