import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator

from farmulator.modules.tasks.models import ScheduledTask, TaskAction, TaskOrigin, TaskStatus

Positive = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=3)]


class TaskState(enum.StrEnum):
    """What a person sees: the stored status, with overdue worked out on the day."""

    DUE = "due"
    OVERDUE = "overdue"
    DONE = "done"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class TaskFilter(enum.StrEnum):
    OPEN = "open"  # due or overdue
    DUE = "due"
    OVERDUE = "overdue"
    DONE = "done"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


def state_of(task: ScheduledTask, today: date) -> TaskState:
    if task.status == TaskStatus.DUE:
        return TaskState.OVERDUE if task.window_end < today else TaskState.DUE
    return TaskState(task.status.value)


class TaskOut(BaseModel):
    id: uuid.UUID
    farm_id: uuid.UUID
    title: str
    action: TaskAction
    due_date: date
    window_end: date
    reminder_on: date | None
    state: TaskState
    origin: TaskOrigin
    cycle_id: uuid.UUID | None
    step_id: uuid.UUID | None
    animal_id: uuid.UUID | None
    group_id: uuid.UUID | None
    product_id: uuid.UUID | None
    dose: Decimal | None
    dose_unit: str | None
    instructions: str | None
    assignee_id: uuid.UUID | None
    done_at: datetime | None
    done_by: uuid.UUID | None
    done_event_id: uuid.UUID | None
    stock_operation_id: uuid.UUID | None
    note: str | None
    skip_reason: str | None
    version: int

    @classmethod
    def build(cls, task: ScheduledTask, today: date) -> "TaskOut":
        fields = {name: getattr(task, name) for name in cls.model_fields if name != "state"}
        return cls(**fields, state=state_of(task, today))


class TaskCreate(BaseModel):
    id: uuid.UUID | None = None
    farm_id: uuid.UUID
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    action: TaskAction = TaskAction.OTHER
    due_date: date
    window_days: int = Field(0, ge=0, le=60)
    reminder_days_before: int = Field(1, ge=0, le=30)
    animal_id: uuid.UUID | None = None
    group_id: uuid.UUID | None = None
    assignee_id: uuid.UUID | None = None
    instructions: str | None = None


class TaskComplete(BaseModel):
    done_at: datetime
    note: str | None = None
    # For vaccinations, treatments and deworming: what was given.
    product_name: str | None = Field(None, description="Defaults to the protocol's product.")
    dose: Positive | None = Field(None, description="Defaults to the protocol's dose.")
    dose_unit: str | None = None
    withdrawal_until: date | None = None
    # Take what was used out of stock, charged to the animal's enterprise.
    quantity_used: Positive | None = None
    stock_location_id: uuid.UUID | None = None

    @field_validator("done_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("must include a timezone")
        return value


class TaskSkip(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]


class Reminders(BaseModel):
    on: date
    reminders: list[TaskOut] = Field(description="Tasks whose reminder day is today.")
    overdue: list[TaskOut] = Field(description="Open tasks past their window: escalate.")
