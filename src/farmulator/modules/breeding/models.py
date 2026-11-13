"""Breeding protocols, breeding cycles and litters.

A protocol is a farm's template for one species: steps such as "pre-farrowing
vaccine, 35 days before the expected birth". Recording a service starts a breeding
cycle; the cycle's dates (service, expected birth, birth, weaning) anchor the steps,
which become scheduled tasks for the dam or her litter.
"""

import enum
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from farmulator.core.models import (
    QUANTITY,
    Base,
    IdMixin,
    SoftDeleteMixin,
    TenantMixin,
    TimestampMixin,
    VersionMixin,
    str_enum,
)
from farmulator.modules.tasks.models import TaskAction


class StepAnchor(enum.StrEnum):
    SERVICE = "service"
    EXPECTED_BIRTH = "expected_birth"
    BIRTH = "birth"
    WEANING = "weaning"


class StepTarget(enum.StrEnum):
    DAM = "dam"
    LITTER = "litter"


class ProtocolTemplate(IdMixin, TenantMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, Base):
    __tablename__ = "protocol_template"

    species_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("species.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_by_vet: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)


class ProtocolStep(IdMixin, TenantMixin, TimestampMixin, Base):
    """One step. ``code`` stays the same when a template is edited, so tasks already
    scheduled from the step can be moved instead of duplicated. Removed steps are
    switched off, not deleted, because done tasks still point at them."""

    __tablename__ = "protocol_step"
    __table_args__ = (UniqueConstraint("template_id", "code"),)

    template_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("protocol_template.id"), index=True)
    code: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(200))
    action: Mapped[TaskAction] = mapped_column(str_enum(TaskAction))
    anchor: Mapped[StepAnchor] = mapped_column(str_enum(StepAnchor))
    offset_days: Mapped[int] = mapped_column(Integer)
    window_days: Mapped[int] = mapped_column(Integer, default=0)
    target: Mapped[StepTarget] = mapped_column(str_enum(StepTarget))
    reminder_days_before: Mapped[int] = mapped_column(Integer, default=1)
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("product.id"))
    dose: Mapped[Decimal | None] = mapped_column(QUANTITY)
    dose_unit: Mapped[str | None] = mapped_column(String(20))
    instructions: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ServiceMethod(enum.StrEnum):
    NATURAL = "natural"
    AI = "ai"


class CycleStatus(enum.StrEnum):
    SERVED = "served"
    PREGNANT = "pregnant"
    BORN = "born"  # litter on the dam, not yet weaned
    WEANED = "weaned"
    NOT_PREGNANT = "not_pregnant"


OPEN_CYCLE_STATUSES = (CycleStatus.SERVED, CycleStatus.PREGNANT, CycleStatus.BORN)


class BreedingCycle(IdMixin, TenantMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "breeding_cycle"
    __table_args__ = (
        # A dam has at most one open cycle: no new service while served, pregnant or
        # still feeding a litter.
        Index(
            "uq_breeding_cycle_open_dam",
            "dam_id",
            unique=True,
            postgresql_where=text("status IN ('served', 'pregnant', 'born')"),
        ),
    )

    farm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("farm.id"), index=True)
    dam_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("animal.id"), index=True)
    sire_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("animal.id"))
    template_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("protocol_template.id"))
    method: Mapped[ServiceMethod] = mapped_column(str_enum(ServiceMethod))
    straw_ref: Mapped[str | None] = mapped_column(String(60))
    service_date: Mapped[date] = mapped_column(Date)
    expected_birth_date: Mapped[date] = mapped_column(Date)
    pregnancy_checked_on: Mapped[date | None] = mapped_column(Date)
    birth_date: Mapped[date | None] = mapped_column(Date)
    weaning_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[CycleStatus] = mapped_column(str_enum(CycleStatus))
    litter_group_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("animal_group.id"))
    note: Mapped[str | None] = mapped_column(Text)


class Litter(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "litter"
    __table_args__ = (
        UniqueConstraint("cycle_id"),
        CheckConstraint(
            "born_alive >= 0 AND born_dead >= 0 AND mummified >= 0", name="counts_not_negative"
        ),
        CheckConstraint("weaned_count IS NULL OR weaned_count >= 0", name="weaned_not_negative"),
    )

    cycle_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("breeding_cycle.id"))
    dam_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("animal.id"), index=True)
    sire_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("animal.id"))
    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("animal_group.id"))
    birth_date: Mapped[date] = mapped_column(Date)
    born_alive: Mapped[int] = mapped_column(Integer)
    born_dead: Mapped[int] = mapped_column(Integer, default=0)
    mummified: Mapped[int] = mapped_column(Integer, default=0)
    weaned_on: Mapped[date | None] = mapped_column(Date)
    weaned_count: Mapped[int | None] = mapped_column(Integer)
    weaning_weight_kg: Mapped[Decimal | None] = mapped_column(QUANTITY)
