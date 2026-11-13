import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from farmulator.modules.stock.models import BaseUnit, LotOrigin, MovementReason, ProductCategory


def _places(places: str) -> AfterValidator:
    """Store and return amounts at a fixed precision, so a reply never depends on how
    the client happened to write the number."""
    step = Decimal(places)
    return AfterValidator(lambda value: value.quantize(step))


Quantity = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=3), _places("0.001")]
UnitCost = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=4), _places("0.0001")]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
OperationId = Annotated[
    uuid.UUID | None,
    Field(description="Optional client-generated id. Sending the same one again is safe."),
]


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("must include a timezone")
    return value


class ProductCreate(BaseModel):
    id: uuid.UUID | None = None
    sku: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    category: ProductCategory
    base_unit: BaseUnit
    is_sellable: bool = False
    reorder_level: Quantity | None = None


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sku: str
    name: str
    category: ProductCategory
    base_unit: BaseUnit
    is_sellable: bool
    reorder_level: Decimal | None
    is_active: bool


class StockLotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    location_id: uuid.UUID
    batch_number: str | None
    expiry_date: date | None
    received_at: datetime
    unit_cost: Decimal
    currency: str
    quantity_on_hand: Decimal
    origin_type: LotOrigin
    origin_id: uuid.UUID | None
    supplier_name: str | None


class ReceiptIn(BaseModel):
    operation_id: OperationId = None
    product_id: uuid.UUID
    location_id: uuid.UUID
    quantity: Quantity
    unit_cost: UnitCost
    currency: Currency
    batch_number: str | None = None
    expiry_date: date | None = None
    supplier_name: str | None = None
    occurred_at: datetime
    opening_balance: bool = Field(
        False, description="Stock already on the farm on its start date, not a purchase."
    )

    _tz = field_validator("occurred_at")(_aware)


class ReceiptOut(BaseModel):
    operation_id: uuid.UUID
    lot: StockLotOut


IssueReason = Literal[
    MovementReason.ISSUE_FEEDING,
    MovementReason.ISSUE_TREATMENT,
    MovementReason.ISSUE_DIPPING,
    MovementReason.ISSUE_FIELD,
    MovementReason.ISSUE_OTHER,
]


class IssueIn(BaseModel):
    operation_id: OperationId = None
    product_id: uuid.UUID
    location_id: uuid.UUID
    quantity: Quantity
    reason: IssueReason
    enterprise_id: uuid.UUID | None = Field(
        None, description="The enterprise this cost is charged to."
    )
    ref_type: str | None = Field(None, max_length=40, examples=["animal_event"])
    ref_id: uuid.UUID | None = None
    occurred_at: datetime
    note: str | None = None

    _tz = field_validator("occurred_at")(_aware)


class Allocation(BaseModel):
    lot_id: uuid.UUID
    quantity: Decimal
    unit_cost: Decimal
    currency: str
    cost: Decimal
    batch_number: str | None
    expiry_date: date | None


class CostTotal(BaseModel):
    currency: str
    amount: Decimal


class IssueOut(BaseModel):
    operation_id: uuid.UUID
    product_id: uuid.UUID
    location_id: uuid.UUID
    quantity: Decimal
    costs: list[CostTotal]
    allocations: list[Allocation]


class TransferIn(BaseModel):
    operation_id: OperationId = None
    product_id: uuid.UUID
    from_location_id: uuid.UUID
    to_location_id: uuid.UUID
    quantity: Quantity
    occurred_at: datetime
    note: str | None = None

    _tz = field_validator("occurred_at")(_aware)


class TransferOut(BaseModel):
    operation_id: uuid.UUID
    allocations: list[Allocation]
    new_lot_ids: list[uuid.UUID]


class AdjustmentIn(BaseModel):
    operation_id: OperationId = None
    lot_id: uuid.UUID
    quantity_delta: Annotated[Decimal, Field(max_digits=14, decimal_places=3), _places("0.001")]
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3)] = Field(
        description="Why: count difference, damage, loss, theft."
    )
    occurred_at: datetime

    _tz = field_validator("occurred_at")(_aware)

    @field_validator("quantity_delta")
    @classmethod
    def _not_zero(cls, value: Decimal) -> Decimal:
        if value == 0:
            raise ValueError("must not be zero")
        return value


class OnHandRow(BaseModel):
    product_id: uuid.UUID
    sku: str
    name: str
    base_unit: BaseUnit
    location_id: uuid.UUID
    currency: str
    quantity: Decimal
    value: Decimal
    below_reorder_level: bool
