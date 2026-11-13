"""Declarative base and the column conventions every table shares.

- ``id``: a UUID the client may generate, so an offline phone can create records
  that never clash.
- ``tenant_id``: on every tenant-owned row; Postgres row-level security filters on it.
- ``created_at`` / ``updated_at``, soft delete through ``deleted_at``, and a ``version``
  counter for optimistic locking on editable records.
- Append-only event tables (animal events, stock movements) skip version and soft delete.
"""

import enum
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, MetaData, Numeric, func
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Money is exact: 14 digits, 2 decimals. Unit costs keep 4 decimals; quantities 3.
MONEY = Numeric(14, 2)
UNIT_COST = Numeric(14, 4)
QUANTITY = Numeric(14, 3)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {Decimal: QUANTITY}  # noqa: RUF012 (SQLAlchemy's documented form)


def str_enum(enum_cls: type[enum.StrEnum]) -> Enum:
    """Store a StrEnum as text with a CHECK constraint (no Postgres ENUM types to migrate)."""
    return Enum(
        enum_cls,
        native_enum=False,
        length=32,
        validate_strings=True,
        values_callable=lambda members: [m.value for m in members],
        name=enum_cls.__name__.lower(),
        create_constraint=True,
    )


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class TenantMixin:
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenant.id", ondelete="RESTRICT"), index=True
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class VersionMixin:
    """Optimistic locking: an update from a stale copy fails instead of overwriting."""

    version: Mapped[int] = mapped_column(Integer, default=1)

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:
        return {"version_id_col": cls.version}
