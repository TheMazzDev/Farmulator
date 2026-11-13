import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from farmulator.modules.livestock.models import (
    AnimalStatus,
    EventSource,
    EventType,
    GroupKind,
    GroupStatus,
    Sex,
    Tracking,
)

Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Positive = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=3)]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]


class SpeciesCreate(BaseModel):
    code: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,39}$")]
    name: ShortText
    default_tracking: Tracking
    gestation_days: int | None = Field(None, gt=0, le=400)


class SpeciesOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    default_tracking: Tracking
    gestation_days: int | None


class BreedCreate(BaseModel):
    species_id: uuid.UUID
    name: ShortText
    society_code: str | None = None


class BreedOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    species_id: uuid.UUID
    name: str
    society_code: str | None


class AnimalCreate(BaseModel):
    id: uuid.UUID | None = Field(None, description="Optional client-generated id.")
    farm_id: uuid.UUID | None = Field(None, description="Leave empty for outside sires.")
    species_id: uuid.UUID
    breed_id: uuid.UUID | None = None
    tag: Tag
    rfid: str | None = None
    name: str | None = None
    sex: Sex
    birth_date: date | None = None
    sire_id: uuid.UUID | None = None
    dam_id: uuid.UUID | None = None
    group_id: uuid.UUID | None = None
    location_id: uuid.UUID | None = None
    enterprise_id: uuid.UUID | None = None
    is_external: bool = False
    society_reg_no: str | None = None

    @model_validator(mode="after")
    def _farm_unless_external(self) -> "AnimalCreate":
        if self.farm_id is None and not self.is_external:
            raise ValueError("farm_id is required unless the animal is external")
        return self


class AnimalUpdate(BaseModel):
    version: int = Field(description="The version you last read; stale edits are refused.")
    tag: Tag | None = None
    rfid: str | None = None
    name: str | None = None
    breed_id: uuid.UUID | None = None
    birth_date: date | None = None
    sire_id: uuid.UUID | None = None
    dam_id: uuid.UUID | None = None
    enterprise_id: uuid.UUID | None = None
    society_reg_no: str | None = None


class AnimalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    farm_id: uuid.UUID | None
    species_id: uuid.UUID
    breed_id: uuid.UUID | None
    tag: str
    rfid: str | None
    name: str | None
    sex: Sex
    birth_date: date | None
    sire_id: uuid.UUID | None
    dam_id: uuid.UUID | None
    group_id: uuid.UUID | None
    location_id: uuid.UUID | None
    enterprise_id: uuid.UUID | None
    status: AnimalStatus
    is_external: bool
    society_reg_no: str | None
    version: int
    created_at: datetime


class GroupCreate(BaseModel):
    id: uuid.UUID | None = None
    farm_id: uuid.UUID
    species_id: uuid.UUID
    breed_id: uuid.UUID | None = None
    name: ShortText
    kind: GroupKind
    head_count: int = Field(ge=0)
    start_date: date
    location_id: uuid.UUID | None = None
    enterprise_id: uuid.UUID | None = None


class GroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    farm_id: uuid.UUID
    species_id: uuid.UUID
    breed_id: uuid.UUID | None
    name: str
    kind: GroupKind
    head_count: int
    start_date: date
    location_id: uuid.UUID | None
    enterprise_id: uuid.UUID | None
    status: GroupStatus
    version: int


# Event payloads: one shape per event type, validated before anything is stored.


class WeightPayload(BaseModel):
    weight_kg: Positive
    method: Literal["scale", "tape", "estimate"] = "scale"


class TreatmentPayload(BaseModel):
    product_name: ShortText
    product_id: uuid.UUID | None = None
    dose: Positive
    dose_unit: Annotated[str, StringConstraints(min_length=1, max_length=20)]
    route: str | None = None
    reason: str | None = None
    withdrawal_until: date | None = Field(
        None, description="No sale or slaughter until after this date (drug residues)."
    )


class MovementPayload(BaseModel):
    to_location_id: uuid.UUID


class SalePayload(BaseModel):
    price: Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]
    currency: Currency
    count: int = Field(1, ge=1)
    buyer: str | None = None
    weight_kg: Positive | None = None


class DeathPayload(BaseModel):
    cause: ShortText
    count: int = Field(1, ge=1)


class SlaughterPayload(BaseModel):
    count: int = Field(1, ge=1)
    live_weight_kg: Positive | None = None


class NotePayload(BaseModel):
    text: Annotated[str, StringConstraints(min_length=1, max_length=2000)]


class _EventBase(BaseModel):
    id: uuid.UUID | None = Field(None, description="Optional client-generated id.")
    animal_id: uuid.UUID | None = None
    group_id: uuid.UUID | None = None
    occurred_at: datetime
    location_id: uuid.UUID | None = None
    source: EventSource = EventSource.MANUAL
    override_withdrawal: bool = Field(
        False, description="Sell or slaughter during a withdrawal period. Needs a reason."
    )
    override_reason: str | None = None

    @model_validator(mode="after")
    def _one_subject(self) -> "_EventBase":
        if (self.animal_id is None) == (self.group_id is None):
            raise ValueError("give exactly one of animal_id or group_id")
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must include a timezone")
        if self.override_withdrawal and not self.override_reason:
            raise ValueError("override_reason is required to override a withdrawal period")
        return self


class WeightEvent(_EventBase):
    type: Literal[EventType.WEIGHT]
    payload: WeightPayload


class TreatmentEvent(_EventBase):
    type: Literal[EventType.TREATMENT, EventType.VACCINATION]
    payload: TreatmentPayload


class MovementEvent(_EventBase):
    type: Literal[EventType.MOVEMENT]
    payload: MovementPayload


class SaleEvent(_EventBase):
    type: Literal[EventType.SALE]
    payload: SalePayload


class DeathEvent(_EventBase):
    type: Literal[EventType.DEATH]
    payload: DeathPayload


class SlaughterEvent(_EventBase):
    type: Literal[EventType.SLAUGHTER]
    payload: SlaughterPayload


class NoteEvent(_EventBase):
    type: Literal[EventType.NOTE]
    payload: NotePayload


AnimalEventCreate = Annotated[
    WeightEvent
    | TreatmentEvent
    | MovementEvent
    | SaleEvent
    | DeathEvent
    | SlaughterEvent
    | NoteEvent,
    Field(discriminator="type"),
]


class AnimalEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    animal_id: uuid.UUID | None
    group_id: uuid.UUID | None
    type: EventType
    occurred_at: datetime
    location_id: uuid.UUID | None
    source: EventSource
    recorded_by: uuid.UUID | None
    payload: dict[str, Any]
    created_at: datetime


class PedigreeNode(BaseModel):
    id: uuid.UUID
    tag: str
    name: str | None
    sex: Sex
    breed_id: uuid.UUID | None
    is_external: bool
    sire: "PedigreeNode | None" = None
    dam: "PedigreeNode | None" = None


class InbreedingOut(BaseModel):
    animal_id: uuid.UUID
    inbreeding_coefficient: Decimal = Field(description="0 to 1; 0.25 is a full-sibling mating.")
    generations_checked: int


class MatingCheckIn(BaseModel):
    sire_id: uuid.UUID
    dam_id: uuid.UUID
    generations: int = Field(5, ge=1, le=10)


class MatingCheckOut(BaseModel):
    sire_id: uuid.UUID
    dam_id: uuid.UUID
    offspring_inbreeding: Decimal
    limit: Decimal
    exceeds_limit: bool
    common_ancestor_ids: list[uuid.UUID]
    generations_checked: int
