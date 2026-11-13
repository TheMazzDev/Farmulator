"""Species, breeds, animals (with sire and dam), groups and the append-only event log."""

import enum
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from farmulator.core.models import (
    Base,
    IdMixin,
    SoftDeleteMixin,
    TenantMixin,
    TimestampMixin,
    VersionMixin,
    str_enum,
    utcnow,
)


class Tracking(enum.StrEnum):
    INDIVIDUAL = "individual"
    GROUP = "group"


class Species(IdMixin, TenantMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "species"
    __table_args__ = (UniqueConstraint("tenant_id", "code"),)

    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(100))
    default_tracking: Mapped[Tracking] = mapped_column(str_enum(Tracking))
    gestation_days: Mapped[int | None] = mapped_column(Integer)


class Breed(IdMixin, TenantMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "breed"
    __table_args__ = (UniqueConstraint("tenant_id", "species_id", "name"),)

    species_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("species.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    society_code: Mapped[str | None] = mapped_column(String(40))


class Sex(enum.StrEnum):
    FEMALE = "female"
    MALE = "male"
    CASTRATED = "castrated"


class AnimalStatus(enum.StrEnum):
    ACTIVE = "active"
    SOLD = "sold"
    DEAD = "dead"
    SLAUGHTERED = "slaughtered"


class Animal(IdMixin, TenantMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, Base):
    """An individually tracked animal. ``sire_id`` and ``dam_id`` point back into this
    table; outside sires (bought bulls, AI straws) are ``is_external`` stub rows."""

    __tablename__ = "animal"
    __table_args__ = (
        Index(
            "uq_animal_tenant_id_tag",
            "tenant_id",
            "tag",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("sire_id IS NULL OR sire_id <> id", name="not_own_sire"),
        CheckConstraint("dam_id IS NULL OR dam_id <> id", name="not_own_dam"),
    )

    farm_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("farm.id"), index=True)
    species_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("species.id"))
    breed_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("breed.id"))
    tag: Mapped[str] = mapped_column(String(60))
    rfid: Mapped[str | None] = mapped_column(String(60))
    name: Mapped[str | None] = mapped_column(String(100))
    sex: Mapped[Sex] = mapped_column(str_enum(Sex))
    birth_date: Mapped[date | None] = mapped_column(Date)
    sire_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("animal.id"), index=True)
    dam_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("animal.id"), index=True)
    group_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("animal_group.id"))
    location_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("location.id"))
    enterprise_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("enterprise.id"))
    status: Mapped[AnimalStatus] = mapped_column(
        str_enum(AnimalStatus), default=AnimalStatus.ACTIVE
    )
    is_external: Mapped[bool] = mapped_column(Boolean, default=False)
    society_reg_no: Mapped[str | None] = mapped_column(String(60))


class GroupKind(enum.StrEnum):
    BATCH = "batch"
    FLOCK = "flock"
    HERD = "herd"
    LITTER = "litter"


class GroupStatus(enum.StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"


class AnimalGroup(IdMixin, TenantMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, Base):
    """Animals managed together and counted, not named: broiler batches, layer flocks."""

    __tablename__ = "animal_group"
    __table_args__ = (CheckConstraint("head_count >= 0", name="head_count_not_negative"),)

    farm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("farm.id"), index=True)
    species_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("species.id"))
    breed_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("breed.id"))
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[GroupKind] = mapped_column(str_enum(GroupKind))
    head_count: Mapped[int] = mapped_column(Integer, default=0)
    start_date: Mapped[date] = mapped_column(Date)
    location_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("location.id"))
    enterprise_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("enterprise.id"))
    status: Mapped[GroupStatus] = mapped_column(str_enum(GroupStatus), default=GroupStatus.ACTIVE)


class EventType(enum.StrEnum):
    WEIGHT = "weight"
    TREATMENT = "treatment"
    VACCINATION = "vaccination"
    MOVEMENT = "movement"
    SALE = "sale"
    DEATH = "death"
    SLAUGHTER = "slaughter"
    NOTE = "note"


class EventSource(enum.StrEnum):
    MANUAL = "manual"
    SENSOR = "sensor"
    DRONE = "drone"
    IMPORT = "import"


class AnimalEvent(IdMixin, TenantMixin, Base):
    """Append-only: events are never edited, so offline devices never conflict.
    A correction is a new event. The app role is granted SELECT and INSERT only."""

    __tablename__ = "animal_event"
    __table_args__ = (
        CheckConstraint("(animal_id IS NULL) <> (group_id IS NULL)", name="one_subject"),
        Index("ix_animal_event_animal_occurred", "animal_id", "occurred_at"),
        Index("ix_animal_event_group_occurred", "group_id", "occurred_at"),
    )

    animal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("animal.id"))
    group_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("animal_group.id"))
    type: Mapped[EventType] = mapped_column(str_enum(EventType))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    location_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("location.id"))
    source: Mapped[EventSource] = mapped_column(str_enum(EventSource), default=EventSource.MANUAL)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column()
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=text("now()")
    )
