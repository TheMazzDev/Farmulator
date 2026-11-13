import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query

from farmulator.core.deps import Ctx, DbSession
from farmulator.core.repo import get_or_404
from farmulator.modules.breeding import reports, service
from farmulator.modules.breeding.models import CycleStatus, ProtocolTemplate
from farmulator.modules.breeding.schemas import (
    BirthIn,
    CycleOut,
    PregnancyCheckIn,
    ServiceIn,
    ServiceOut,
    TemplateCreate,
    TemplateOut,
    TemplateUpdate,
    WeaningIn,
)

router = APIRouter(prefix="/v1", tags=["breeding"])

OnDay = Annotated[date | None, Query(description="Judge overdue tasks on this day.")]


def _today(on: date | None = None) -> date:
    return on or datetime.now(UTC).date()


@router.get("/protocols", response_model=list[TemplateOut])
async def list_protocols(
    session: DbSession, species_id: uuid.UUID | None = None
) -> list[TemplateOut]:
    templates = await service.list_templates(session, species_id)
    return [await service.template_out(session, t) for t in templates]


@router.post("/protocols", response_model=TemplateOut, status_code=201)
async def create_protocol(body: TemplateCreate, session: DbSession, ctx: Ctx) -> TemplateOut:
    template = await service.create_template(session, ctx, body)
    await session.commit()
    return await service.template_out(session, template)


@router.get("/protocols/{template_id}", response_model=TemplateOut)
async def get_protocol(template_id: uuid.UUID, session: DbSession) -> TemplateOut:
    template = await get_or_404(session, ProtocolTemplate, template_id, "protocol")
    return await service.template_out(session, template)


@router.put("/protocols/{template_id}", response_model=TemplateOut)
async def update_protocol(
    template_id: uuid.UUID, body: TemplateUpdate, session: DbSession, ctx: Ctx
) -> TemplateOut:
    """Replace the steps. Running cycles' open tasks follow the new timings. The vet
    approval is cleared unless this request sets approved_by_vet again."""
    template = await service.update_template(session, ctx, template_id, body)
    await session.commit()
    return await service.template_out(session, template)


@router.post("/breeding/services", response_model=ServiceOut, status_code=201)
async def record_service(body: ServiceIn, session: DbSession, ctx: Ctx) -> ServiceOut:
    """Record a service or AI. Starts a breeding cycle and schedules its tasks."""
    result = await service.record_service(session, ctx, body)
    await session.commit()
    return result


@router.get("/breeding/cycles", response_model=list[CycleOut])
async def list_cycles(
    session: DbSession,
    farm_id: uuid.UUID | None = None,
    dam_id: uuid.UUID | None = None,
    status: CycleStatus | None = None,
) -> list[CycleOut]:
    cycles = await service.list_cycles(session, farm_id=farm_id, dam_id=dam_id, status=status)
    today = _today()
    return [await service.cycle_out(session, c, today, with_tasks=False) for c in cycles]


@router.get("/breeding/cycles/{cycle_id}", response_model=CycleOut)
async def get_cycle(cycle_id: uuid.UUID, session: DbSession, on: OnDay = None) -> CycleOut:
    return await service.cycle_out(session, await service.get_cycle(session, cycle_id), _today(on))


@router.post("/breeding/cycles/{cycle_id}/pregnancy-check", response_model=CycleOut)
async def pregnancy_check(
    cycle_id: uuid.UUID, body: PregnancyCheckIn, session: DbSession, ctx: Ctx
) -> CycleOut:
    """Pregnant: carry on. Not pregnant: the cycle closes and its open tasks are cancelled."""
    cycle = await service.record_pregnancy_check(session, ctx, cycle_id, body)
    await session.commit()
    return await service.cycle_out(session, cycle, _today())


@router.post("/breeding/cycles/{cycle_id}/birth", response_model=CycleOut)
async def record_birth(
    cycle_id: uuid.UUID, body: BirthIn, session: DbSession, ctx: Ctx
) -> CycleOut:
    """Record the litter. Creates the litter group and schedules its tasks from the real
    birth date."""
    cycle = await service.record_birth(session, ctx, cycle_id, body)
    await session.commit()
    return await service.cycle_out(session, cycle, _today())


@router.post("/breeding/cycles/{cycle_id}/weaning", response_model=CycleOut)
async def record_weaning(
    cycle_id: uuid.UUID, body: WeaningIn, session: DbSession, ctx: Ctx
) -> CycleOut:
    cycle = await service.record_weaning(session, ctx, cycle_id, body)
    await session.commit()
    return await service.cycle_out(session, cycle, _today())


@router.get(
    "/reports/breeding/farrowings-due",
    response_model=list[reports.FarrowingDue],
    tags=["reports"],
)
async def farrowings_due(
    session: DbSession,
    from_date: date | None = None,
    to_date: date | None = None,
    farm_id: uuid.UUID | None = None,
    on: OnDay = None,
) -> list[reports.FarrowingDue]:
    """Sows due to farrow; this week and next by default."""
    today = _today(on)
    start = from_date or today
    end = to_date or start + timedelta(days=13)
    return await reports.farrowings_due(session, start, end, today, farm_id)


@router.get("/reports/breeding/litters", response_model=reports.LitterReport, tags=["reports"])
async def litter_performance(
    session: DbSession, from_date: date, to_date: date, farm_id: uuid.UUID | None = None
) -> reports.LitterReport:
    """Born alive, born dead, weaned and pre-weaning mortality for litters born in a period."""
    return await reports.litter_performance(session, from_date, to_date, farm_id)


@router.get("/reports/tasks/compliance", response_model=reports.ComplianceReport, tags=["reports"])
async def task_compliance(
    session: DbSession,
    from_date: date,
    to_date: date,
    farm_id: uuid.UUID | None = None,
    on: OnDay = None,
) -> reports.ComplianceReport:
    """Of the tasks due in the period, how many were done within their window."""
    return await reports.task_compliance(session, from_date, to_date, _today(on), farm_id)
