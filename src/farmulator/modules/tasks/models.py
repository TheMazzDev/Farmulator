"""Scheduled tasks: the one to-do list for breeding protocols, recurring work and
manual jobs. "Overdue" is not stored: it is a due task whose window has passed."""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from farmulator.core.models import (
    QUANTITY,
    Base,
    IdMixin,
    TenantMixin,
    TimestampMixin,
    VersionMixin,
    str_enum,
)


class TaskAction(enum.StrEnum):
    VACCINATE = "vaccinate"
    TREAT = "treat"
    DEWORM = "deworm"
    MOVE = "move"
    PREPARE = "prepare"
    CHECK = "check"
    # Recorded through the breeding cycle, which ticks these tasks off itself.
    PREGNANCY_CHECK = "pregnancy_check"
    BIRTH = "birth"
    WEANING = "weaning"
    OTHER = "other"


MEDICAL_ACTIONS = {TaskAction.VACCINATE, TaskAction.TREAT, TaskAction.DEWORM}
CYCLE_ACTIONS = {TaskAction.PREGNANCY_CHECK, TaskAction.BIRTH, TaskAction.WEANING}


class TaskStatus(enum.StrEnum):
    DUE = "due"
    DONE = "done"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class TaskOrigin(enum.StrEnum):
    PROTOCOL = "protocol"
    MANUAL = "manual"


class ScheduledTask(IdMixin, TenantMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "scheduled_task"
    __table_args__ = (
        # A protocol step creates at most one task per breeding cycle.
        Index(
            "uq_scheduled_task_cycle_step",
            "cycle_id",
            "step_id",
            unique=True,
            postgresql_where=text("cycle_id IS NOT NULL"),
        ),
        Index("ix_scheduled_task_farm_due", "farm_id", "status", "due_date"),
        Index("ix_scheduled_task_reminder", "reminder_on"),
    )

    farm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("farm.id"))
    title: Mapped[str] = mapped_column(String(200))
    action: Mapped[TaskAction] = mapped_column(str_enum(TaskAction))
    due_date: Mapped[date] = mapped_column(Date)
    window_end: Mapped[date] = mapped_column(Date)
    reminder_on: Mapped[date | None] = mapped_column(Date)
    status: Mapped[TaskStatus] = mapped_column(str_enum(TaskStatus), default=TaskStatus.DUE)
    origin: Mapped[TaskOrigin] = mapped_column(str_enum(TaskOrigin))
    cycle_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("breeding_cycle.id"), index=True)
    step_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("protocol_step.id"))
    animal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("animal.id"), index=True)
    group_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("animal_group.id"))
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("product.id"))
    dose: Mapped[Decimal | None] = mapped_column(QUANTITY)
    dose_unit: Mapped[str | None] = mapped_column(String(20))
    instructions: Mapped[str | None] = mapped_column(Text)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column()
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    done_by: Mapped[uuid.UUID | None] = mapped_column()
    done_event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("animal_event.id"))
    stock_operation_id: Mapped[uuid.UUID | None] = mapped_column()
    note: Mapped[str | None] = mapped_column(Text)
    skip_reason: Mapped[str | None] = mapped_column(Text)
