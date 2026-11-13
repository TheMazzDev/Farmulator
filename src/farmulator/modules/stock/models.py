"""Products, stock lots and the stock movement ledger.

Every physical good (feed, vaccines, dip chemicals, beef cuts) is held as lots at a
location. Each lot remembers its cost, batch, expiry and where it came from. The
movement ledger is append-only: stock on hand is the sum of a lot's movements.
``stock_lot.quantity_on_hand`` caches that sum so picking lots stays fast; it is
only changed in the same transaction as the movement, under a row lock.
"""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from farmulator.core.models import (
    QUANTITY,
    UNIT_COST,
    Base,
    IdMixin,
    TenantMixin,
    TimestampMixin,
    VersionMixin,
    str_enum,
    utcnow,
)


class ProductCategory(enum.StrEnum):
    FEED_INGREDIENT = "feed_ingredient"
    FEED = "feed"
    VET_DRUG = "vet_drug"
    VACCINE = "vaccine"
    DIP_CHEMICAL = "dip_chemical"
    CROP_INPUT = "crop_input"
    PRODUCE = "produce"
    MEAT_CUT = "meat_cut"
    EGG = "egg"
    OTHER = "other"


class BaseUnit(enum.StrEnum):
    KG = "kg"
    G = "g"
    L = "l"
    ML = "ml"
    DOSE = "dose"
    UNIT = "unit"


class Product(IdMixin, TenantMixin, TimestampMixin, VersionMixin, Base):
    __tablename__ = "product"
    __table_args__ = (UniqueConstraint("tenant_id", "sku"),)

    sku: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[ProductCategory] = mapped_column(str_enum(ProductCategory))
    base_unit: Mapped[BaseUnit] = mapped_column(str_enum(BaseUnit))
    is_sellable: Mapped[bool] = mapped_column(Boolean, default=False)
    reorder_level: Mapped[Decimal | None] = mapped_column(QUANTITY)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class LotOrigin(enum.StrEnum):
    OPENING = "opening"
    PURCHASE = "purchase"
    PRODUCTION = "production"
    HARVEST = "harvest"
    CARCASS = "carcass"
    TRANSFER = "transfer"


class StockLot(IdMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "stock_lot"
    __table_args__ = (
        CheckConstraint("quantity_on_hand >= 0", name="on_hand_not_negative"),
        Index("ix_stock_lot_pick", "product_id", "location_id", "expiry_date", "received_at"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product.id"))
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("location.id"))
    batch_number: Mapped[str | None] = mapped_column(String(60))
    expiry_date: Mapped[date | None] = mapped_column(Date)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    unit_cost: Mapped[Decimal] = mapped_column(UNIT_COST)
    currency: Mapped[str] = mapped_column(String(3))
    quantity_on_hand: Mapped[Decimal] = mapped_column(QUANTITY)
    origin_type: Mapped[LotOrigin] = mapped_column(str_enum(LotOrigin))
    origin_id: Mapped[uuid.UUID | None] = mapped_column()
    supplier_name: Mapped[str | None] = mapped_column(String(200))


class MovementReason(enum.StrEnum):
    OPENING = "opening"
    RECEIPT = "receipt"
    ISSUE_FEEDING = "issue_feeding"
    ISSUE_TREATMENT = "issue_treatment"
    ISSUE_DIPPING = "issue_dipping"
    ISSUE_FIELD = "issue_field"
    ISSUE_OTHER = "issue_other"
    TRANSFER_OUT = "transfer_out"
    TRANSFER_IN = "transfer_in"
    ADJUSTMENT = "adjustment"
    SALE = "sale"
    PRODUCTION_IN = "production_in"
    PRODUCTION_OUT = "production_out"


class StockMovement(IdMixin, TenantMixin, Base):
    """Append-only ledger line. Positive quantity in, negative out. One request's lines
    share an ``operation_id``, which also makes a retried request safe to replay.
    ``enterprise_id`` is where the cost lands in profit and loss."""

    __tablename__ = "stock_movement"
    __table_args__ = (
        CheckConstraint("quantity <> 0", name="quantity_not_zero"),
        Index("ix_stock_movement_operation", "operation_id"),
        Index("ix_stock_movement_lot", "lot_id"),
        Index("ix_stock_movement_enterprise_occurred", "enterprise_id", "occurred_at"),
    )

    operation_id: Mapped[uuid.UUID] = mapped_column()
    lot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stock_lot.id"))
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("product.id"))
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("location.id"))
    quantity: Mapped[Decimal] = mapped_column(QUANTITY)
    unit_cost: Mapped[Decimal] = mapped_column(UNIT_COST)
    currency: Mapped[str] = mapped_column(String(3))
    reason: Mapped[MovementReason] = mapped_column(str_enum(MovementReason))
    enterprise_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("enterprise.id"))
    ref_type: Mapped[str | None] = mapped_column(String(40))
    ref_id: Mapped[uuid.UUID | None] = mapped_column()
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_by: Mapped[uuid.UUID | None] = mapped_column()
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=text("now()")
    )
