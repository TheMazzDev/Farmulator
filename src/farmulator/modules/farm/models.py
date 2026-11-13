"""Tenants, farms, the location tree and enterprises (the profit centres)."""

import enum
import uuid
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from farmulator.core.models import (
    Base,
    IdMixin,
    SoftDeleteMixin,
    TenantMixin,
    TimestampMixin,
    VersionMixin,
    str_enum,
)


class Tenant(IdMixin, TimestampMixin, VersionMixin, Base):
    """One customer business. It may own several farms."""

    __tablename__ = "tenant"

    name: Mapped[str] = mapped_column(String(200))
    country: Mapped[str] = mapped_column(String(2))  # ISO 3166-1 alpha-2
    base_currency: Mapped[str] = mapped_column(String(3))  # ISO 4217
    plan: Mapped[str] = mapped_column(String(40), default="trial")
    # Warn when a planned mating's offspring would be more inbred than this.
    inbreeding_limit: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("0.0625"))


class Farm(IdMixin, TenantMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, Base):
    __tablename__ = "farm"

    name: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64), default="Africa/Harare")
    notes: Mapped[str | None] = mapped_column(Text)


class EnterpriseKind(enum.StrEnum):
    LIVESTOCK = "livestock"
    CROP = "crop"
    FEED_MILL = "feed_mill"
    RETAIL = "retail"
    RESTAURANT = "restaurant"
    OVERHEAD = "overhead"


class Enterprise(IdMixin, TenantMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, Base):
    """A profit centre (breeding sows, broilers, maize 2026, butchery). Costs and sales
    are tagged with one, so profit per enterprise needs no sorting out later."""

    __tablename__ = "enterprise"

    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[EnterpriseKind] = mapped_column(str_enum(EnterpriseKind))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("enterprise.id"))
    species_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("species.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class LocationKind(enum.StrEnum):
    PADDOCK = "paddock"
    PEN = "pen"
    PIG_HOUSE = "pig_house"
    POULTRY_HOUSE = "poultry_house"
    FIELD = "field"
    BOREHOLE = "borehole"
    STORE_ROOM = "store_room"
    FEED_STORE = "feed_store"
    COLD_ROOM = "cold_room"
    BUTCHERY = "butchery"
    SHOP = "shop"
    RESTAURANT = "restaurant"
    OTHER = "other"


class Location(IdMixin, TenantMixin, TimestampMixin, SoftDeleteMixin, VersionMixin, Base):
    """Everything happens somewhere. One tree covers paddocks, pens, stores and shops,
    so animal moves and stock transfers use the same reference."""

    __tablename__ = "location"

    farm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("farm.id"), index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("location.id"))
    kind: Mapped[LocationKind] = mapped_column(str_enum(LocationKind))
    name: Mapped[str] = mapped_column(String(200))
    capacity: Mapped[int | None] = mapped_column(Integer)
    enterprise_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("enterprise.id"))
