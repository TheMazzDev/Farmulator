import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from farmulator.modules.farm.models import EnterpriseKind, LocationKind

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
CountryCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]
CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]


class TenantCreate(BaseModel):
    name: Name
    country: CountryCode = Field(examples=["ZW"])
    base_currency: CurrencyCode = Field(examples=["USD"])


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    country: str
    base_currency: str
    plan: str
    inbreeding_limit: Decimal
    created_at: datetime


class FarmCreate(BaseModel):
    id: uuid.UUID | None = Field(None, description="Optional client-generated id.")
    name: Name
    timezone: str = "Africa/Harare"
    notes: str | None = None


class FarmUpdate(BaseModel):
    name: Name | None = None
    timezone: str | None = None
    notes: str | None = None
    version: int = Field(description="The version you last read; stale edits are refused.")


class FarmOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    timezone: str
    notes: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class EnterpriseCreate(BaseModel):
    id: uuid.UUID | None = None
    name: Name
    kind: EnterpriseKind
    parent_id: uuid.UUID | None = None
    species_id: uuid.UUID | None = None


class EnterpriseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    kind: EnterpriseKind
    parent_id: uuid.UUID | None
    species_id: uuid.UUID | None
    is_active: bool
    version: int


class LocationCreate(BaseModel):
    id: uuid.UUID | None = None
    farm_id: uuid.UUID
    parent_id: uuid.UUID | None = None
    kind: LocationKind
    name: Name
    capacity: int | None = Field(None, ge=0)
    enterprise_id: uuid.UUID | None = None


class LocationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    farm_id: uuid.UUID
    parent_id: uuid.UUID | None
    kind: LocationKind
    name: str
    capacity: int | None
    enterprise_id: uuid.UUID | None
    version: int
