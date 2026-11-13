import uuid
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Query

from farmulator.core.deps import Ctx, DbSession
from farmulator.modules.tasks import service
from farmulator.modules.tasks.schemas import (
    Reminders,
    TaskComplete,
    TaskCreate,
    TaskFilter,
    TaskOut,
    TaskSkip,
)

router = APIRouter(prefix="/v1", tags=["tasks"])

OnDay = Annotated[
    date | None,
    Query(
        description="The day to judge overdue tasks on, in the farm's time zone. "
        "Defaults to today (UTC)."
    ),
]


def _today(on: date | None) -> date:
    return on or datetime.now(UTC).date()


@router.get("/tasks", response_model=list[TaskOut])
async def list_tasks(
    session: DbSession,
    farm_id: uuid.UUID | None = None,
    state: TaskFilter | None = None,
    due_from: date | None = None,
    due_to: date | None = None,
    animal_id: uuid.UUID | None = None,
    group_id: uuid.UUID | None = None,
    cycle_id: uuid.UUID | None = None,
    assignee_id: uuid.UUID | None = None,
    on: OnDay = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[TaskOut]:
    today = _today(on)
    tasks = await service.list_tasks(
        session,
        today=today,
        farm_id=farm_id,
        state=state,
        due_from=due_from,
        due_to=due_to,
        animal_id=animal_id,
        group_id=group_id,
        cycle_id=cycle_id,
        assignee_id=assignee_id,
        limit=limit,
    )
    return [TaskOut.build(t, today) for t in tasks]


@router.post("/tasks", response_model=TaskOut, status_code=201)
async def create_task(body: TaskCreate, session: DbSession, ctx: Ctx) -> TaskOut:
    task = await service.create_task(session, ctx, body)
    await session.commit()
    return TaskOut.build(task, _today(None))


@router.get("/tasks/reminders", response_model=Reminders)
async def task_reminders(
    session: DbSession, on: OnDay = None, farm_id: uuid.UUID | None = None
) -> Reminders:
    """Reminders due today and overdue tasks, for the WhatsApp and SMS sender."""
    today = _today(on)
    due, late = await service.reminders(session, today, farm_id)
    return Reminders(
        on=today,
        reminders=[TaskOut.build(t, today) for t in due],
        overdue=[TaskOut.build(t, today) for t in late],
    )


@router.get("/tasks/{task_id}", response_model=TaskOut)
async def get_task(task_id: uuid.UUID, session: DbSession, on: OnDay = None) -> TaskOut:
    return TaskOut.build(await service.get_task(session, task_id), _today(on))


@router.post("/tasks/{task_id}/complete", response_model=TaskOut)
async def complete_task(
    task_id: uuid.UUID, body: TaskComplete, session: DbSession, ctx: Ctx
) -> TaskOut:
    task = await service.complete_task(session, ctx, task_id, body)
    await session.commit()
    return TaskOut.build(task, body.done_at.date())


@router.post("/tasks/{task_id}/skip", response_model=TaskOut)
async def skip_task(task_id: uuid.UUID, body: TaskSkip, session: DbSession, ctx: Ctx) -> TaskOut:
    task = await service.skip_task(session, ctx, task_id, body.reason)
    await session.commit()
    return TaskOut.build(task, _today(None))
