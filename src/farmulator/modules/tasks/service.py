import uuid
from collections.abc import Sequence
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from farmulator.core.deps import RequestContext
from farmulator.core.errors import Conflict, Invalid, NotFound
from farmulator.core.repo import get_ref
from farmulator.modules.farm.models import Farm
from farmulator.modules.livestock import service as livestock_service
from farmulator.modules.livestock.models import Animal, AnimalGroup, EventType
from farmulator.modules.livestock.schemas import TreatmentEvent, TreatmentPayload
from farmulator.modules.stock import service as stock_service
from farmulator.modules.stock.models import MovementReason, Product
from farmulator.modules.stock.schemas import IssueIn
from farmulator.modules.tasks.models import (
    CYCLE_ACTIONS,
    MEDICAL_ACTIONS,
    ScheduledTask,
    TaskAction,
    TaskOrigin,
    TaskStatus,
)
from farmulator.modules.tasks.schemas import TaskComplete, TaskCreate, TaskFilter

MAX_TASKS = 500


async def _locked(session: AsyncSession, task_id: uuid.UUID) -> ScheduledTask:
    stmt = select(ScheduledTask).where(ScheduledTask.id == task_id).with_for_update()
    task = (await session.execute(stmt)).scalar_one_or_none()
    if task is None:
        raise NotFound(f"No task with id {task_id}.", code="task_not_found")
    return task


async def list_tasks(
    session: AsyncSession,
    *,
    today: date,
    farm_id: uuid.UUID | None = None,
    state: TaskFilter | None = None,
    due_from: date | None = None,
    due_to: date | None = None,
    animal_id: uuid.UUID | None = None,
    group_id: uuid.UUID | None = None,
    cycle_id: uuid.UUID | None = None,
    assignee_id: uuid.UUID | None = None,
    limit: int = 200,
) -> Sequence[ScheduledTask]:
    stmt = select(ScheduledTask)
    if state in (TaskFilter.OPEN, TaskFilter.DUE, TaskFilter.OVERDUE):
        stmt = stmt.where(ScheduledTask.status == TaskStatus.DUE)
        if state == TaskFilter.DUE:
            stmt = stmt.where(ScheduledTask.window_end >= today)
        elif state == TaskFilter.OVERDUE:
            stmt = stmt.where(ScheduledTask.window_end < today)
    elif state is not None:
        stmt = stmt.where(ScheduledTask.status == TaskStatus(state.value))
    filters = {
        ScheduledTask.farm_id: farm_id,
        ScheduledTask.animal_id: animal_id,
        ScheduledTask.group_id: group_id,
        ScheduledTask.cycle_id: cycle_id,
        ScheduledTask.assignee_id: assignee_id,
    }
    for column, value in filters.items():
        if value is not None:
            stmt = stmt.where(column == value)
    if due_from is not None:
        stmt = stmt.where(ScheduledTask.due_date >= due_from)
    if due_to is not None:
        stmt = stmt.where(ScheduledTask.due_date <= due_to)
    stmt = stmt.order_by(ScheduledTask.due_date, ScheduledTask.title, ScheduledTask.id)
    return (await session.execute(stmt.limit(min(limit, MAX_TASKS)))).scalars().all()


async def get_task(session: AsyncSession, task_id: uuid.UUID) -> ScheduledTask:
    task = await session.get(ScheduledTask, task_id)
    if task is None:
        raise NotFound(f"No task with id {task_id}.", code="task_not_found")
    return task


async def create_task(
    session: AsyncSession, ctx: RequestContext, data: TaskCreate
) -> ScheduledTask:
    if data.id is not None and (existing := await session.get(ScheduledTask, data.id)):
        return existing
    if data.action in CYCLE_ACTIONS:
        raise Invalid("Pregnancy checks, births and weaning come from breeding records.")
    await get_ref(session, Farm, data.farm_id, "farm")
    await get_ref(session, Animal, data.animal_id, "animal")
    await get_ref(session, AnimalGroup, data.group_id, "animal group")
    task = ScheduledTask(
        tenant_id=ctx.tenant_id,
        farm_id=data.farm_id,
        title=data.title,
        action=data.action,
        due_date=data.due_date,
        window_end=date.fromordinal(data.due_date.toordinal() + data.window_days),
        reminder_on=date.fromordinal(data.due_date.toordinal() - data.reminder_days_before),
        origin=TaskOrigin.MANUAL,
        animal_id=data.animal_id,
        group_id=data.group_id,
        assignee_id=data.assignee_id,
        instructions=data.instructions,
    )
    if data.id is not None:
        task.id = data.id
    session.add(task)
    await session.flush()
    return task


async def complete_task(
    session: AsyncSession, ctx: RequestContext, task_id: uuid.UUID, data: TaskComplete
) -> ScheduledTask:
    """Tick a task off. For a vaccination, treatment or deworming this also records the
    animal event and, when a quantity is given, takes it out of stock. Retrying is safe:
    the event and stock issue reuse ids derived from the task."""
    task = await _locked(session, task_id)
    if task.status == TaskStatus.DONE:
        return task
    if task.status != TaskStatus.DUE:
        raise Conflict(f"This task is already {task.status.value}.", code="task_closed")
    if task.action in CYCLE_ACTIONS:
        raise Conflict(
            "Record this on the breeding cycle; the task is ticked off automatically.",
            code="use_breeding_cycle",
        )

    enterprise_id = None
    if task.animal_id is not None:
        animal = await session.get(Animal, task.animal_id)
        enterprise_id = animal.enterprise_id if animal else None
    elif task.group_id is not None:
        group = await session.get(AnimalGroup, task.group_id)
        enterprise_id = group.enterprise_id if group else None

    if task.action in MEDICAL_ACTIONS and (task.animal_id or task.group_id):
        product = await session.get(Product, task.product_id) if task.product_id else None
        name = data.product_name or (product.name if product else None)
        dose = data.dose or task.dose
        unit = data.dose_unit or task.dose_unit
        if not name or dose is None or not unit:
            raise Invalid(
                "Say what was given: product_name, dose and dose_unit.",
                code="treatment_details_required",
            )
        event = await livestock_service.record_event(
            session,
            ctx,
            TreatmentEvent(
                id=uuid.uuid5(task.id, "event"),
                animal_id=task.animal_id,
                group_id=task.group_id,
                occurred_at=data.done_at,
                type=(
                    EventType.VACCINATION
                    if task.action == TaskAction.VACCINATE
                    else EventType.TREATMENT
                ),
                payload=TreatmentPayload(
                    product_name=name,
                    product_id=task.product_id,
                    dose=dose,
                    dose_unit=unit,
                    reason=task.title,
                    withdrawal_until=data.withdrawal_until,
                ),
            ),
        )
        task.done_event_id = event.id

    if data.quantity_used is not None:
        if task.product_id is None or data.stock_location_id is None:
            raise Invalid(
                "To take stock out, the task needs a product and you need stock_location_id.",
                code="stock_details_required",
            )
        operation_id = uuid.uuid5(task.id, "stock")
        await stock_service.issue(
            session,
            ctx,
            IssueIn(
                operation_id=operation_id,
                product_id=task.product_id,
                location_id=data.stock_location_id,
                quantity=data.quantity_used,
                reason=MovementReason.ISSUE_TREATMENT,
                enterprise_id=enterprise_id,
                ref_type="scheduled_task",
                ref_id=task.id,
                occurred_at=data.done_at,
                note=task.title,
            ),
        )
        task.stock_operation_id = operation_id

    task.status = TaskStatus.DONE
    task.done_at = data.done_at
    task.done_by = ctx.user_id
    task.note = data.note
    await session.flush()
    return task


async def skip_task(
    session: AsyncSession, ctx: RequestContext, task_id: uuid.UUID, reason: str
) -> ScheduledTask:
    task = await _locked(session, task_id)
    if task.status != TaskStatus.DUE:
        raise Conflict(f"This task is already {task.status.value}.", code="task_closed")
    task.status = TaskStatus.SKIPPED
    task.skip_reason = reason
    task.done_by = ctx.user_id
    await session.flush()
    return task


async def reminders(
    session: AsyncSession, on: date, farm_id: uuid.UUID | None
) -> tuple[Sequence[ScheduledTask], Sequence[ScheduledTask]]:
    """What a notification worker sends on a given day: reminders, and overdue tasks to
    escalate to the manager."""
    base = select(ScheduledTask).where(ScheduledTask.status == TaskStatus.DUE)
    if farm_id is not None:
        base = base.where(ScheduledTask.farm_id == farm_id)
    order = (ScheduledTask.due_date, ScheduledTask.title)
    due = await session.execute(base.where(ScheduledTask.reminder_on == on).order_by(*order))
    late = await session.execute(base.where(ScheduledTask.window_end < on).order_by(*order))
    return due.scalars().all(), late.scalars().all()
