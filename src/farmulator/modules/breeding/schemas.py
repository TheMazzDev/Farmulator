import uuid
from collections import Counter
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from farmulator.modules.breeding.models import (
    CycleStatus,
    ServiceMethod,
    StepAnchor,
    StepTarget,
)
from farmulator.modules.livestock.schemas import MatingCheckOut
from farmulator.modules.tasks.models import CYCLE_ACTIONS, TaskAction
from farmulator.modules.tasks.schemas import TaskOut

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Code = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,59}$")]

# Where each breeding-record step must sit.
CYCLE_ACTION_RULES = {
    TaskAction.PREGNANCY_CHECK: {StepAnchor.SERVICE},
    TaskAction.BIRTH: {StepAnchor.SERVICE, StepAnchor.EXPECTED_BIRTH},
    TaskAction.WEANING: {StepAnchor.BIRTH},
}


class StepIn(BaseModel):
    code: Code = Field(description="Stable key, e.g. 'pre-farrow-vaccine-1'.")
    name: Name
    action: TaskAction
    anchor: StepAnchor
    offset_days: int = Field(
        ge=-400, le=400, description="Days after the anchor (negative: before)."
    )
    window_days: int = Field(0, ge=0, le=60, description="Days allowed after the due date.")
    target: StepTarget = StepTarget.DAM
    reminder_days_before: int = Field(1, ge=0, le=30)
    product_id: uuid.UUID | None = None
    dose: Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=3)] | None = None
    dose_unit: Annotated[str, StringConstraints(max_length=20)] | None = None
    instructions: str | None = None

    @model_validator(mode="after")
    def _fits_the_cycle(self) -> "StepIn":
        if self.target == StepTarget.LITTER and self.anchor not in (
            StepAnchor.BIRTH,
            StepAnchor.WEANING,
        ):
            raise ValueError("litter steps must be timed from the birth or weaning")
        if self.action in CYCLE_ACTIONS:
            if self.target != StepTarget.DAM:
                raise ValueError(f"a {self.action.value} step is for the dam")
            if self.anchor not in CYCLE_ACTION_RULES[self.action]:
                allowed = ", ".join(sorted(a.value for a in CYCLE_ACTION_RULES[self.action]))
                raise ValueError(f"a {self.action.value} step must be timed from: {allowed}")
        return self


def _check_steps(steps: list[StepIn]) -> list[StepIn]:
    codes = Counter(s.code for s in steps)
    if repeated := [c for c, n in codes.items() if n > 1]:
        raise ValueError(f"step codes must be unique: {', '.join(repeated)}")
    actions = Counter(s.action for s in steps if s.action in CYCLE_ACTIONS)
    if repeated := [a.value for a, n in actions.items() if n > 1]:
        raise ValueError(f"only one step each for: {', '.join(repeated)}")
    return steps


class TemplateCreate(BaseModel):
    species_id: uuid.UUID
    name: Name
    is_default: bool = False
    approved_by_vet: bool = False
    notes: str | None = None
    steps: list[StepIn] = Field(min_length=1, max_length=60)

    @model_validator(mode="after")
    def _steps(self) -> "TemplateCreate":
        _check_steps(self.steps)
        return self


class TemplateUpdate(BaseModel):
    """Replace the template's steps. Open tasks in running cycles are moved to the new
    timings, removed steps' tasks are cancelled, and new steps are scheduled."""

    version: int
    name: Name | None = None
    is_default: bool | None = None
    approved_by_vet: bool | None = None
    notes: str | None = None
    steps: list[StepIn] = Field(min_length=1, max_length=60)

    @model_validator(mode="after")
    def _steps(self) -> "TemplateUpdate":
        _check_steps(self.steps)
        return self


class StepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    action: TaskAction
    anchor: StepAnchor
    offset_days: int
    window_days: int
    target: StepTarget
    reminder_days_before: int
    product_id: uuid.UUID | None
    dose: Decimal | None
    dose_unit: str | None
    instructions: str | None


class TemplateOut(BaseModel):
    id: uuid.UUID
    species_id: uuid.UUID
    name: str
    is_default: bool
    approved_by_vet: bool
    notes: str | None
    version: int
    steps: list[StepOut]


class ServiceIn(BaseModel):
    id: uuid.UUID | None = Field(None, description="Optional client-generated cycle id.")
    dam_id: uuid.UUID
    sire_id: uuid.UUID | None = Field(None, description="Leave empty for AI from a straw.")
    method: ServiceMethod = ServiceMethod.NATURAL
    straw_ref: str | None = None
    service_date: date
    template_id: uuid.UUID | None = Field(
        None, description="Defaults to the species' default protocol."
    )
    note: str | None = None


class PregnancyCheckIn(BaseModel):
    checked_on: date
    result: Literal["pregnant", "not_pregnant"]
    note: str | None = None


class BirthIn(BaseModel):
    birth_date: date
    born_alive: int = Field(ge=0, le=40)
    born_dead: int = Field(0, ge=0, le=40)
    mummified: int = Field(0, ge=0, le=40)
    location_id: uuid.UUID | None = Field(None, description="Defaults to the dam's location.")


class WeaningIn(BaseModel):
    weaning_date: date
    weaned_count: int = Field(ge=0, le=40)
    total_weight_kg: Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=3)] | None = None


class LitterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    group_id: uuid.UUID
    birth_date: date
    born_alive: int
    born_dead: int
    mummified: int
    weaned_on: date | None
    weaned_count: int | None
    weaning_weight_kg: Decimal | None


class CycleOut(BaseModel):
    id: uuid.UUID
    farm_id: uuid.UUID
    dam_id: uuid.UUID
    sire_id: uuid.UUID | None
    template_id: uuid.UUID
    method: ServiceMethod
    straw_ref: str | None
    service_date: date
    expected_birth_date: date
    pregnancy_checked_on: date | None
    birth_date: date | None
    weaning_date: date | None
    status: CycleStatus
    litter_group_id: uuid.UUID | None
    note: str | None
    version: int
    litter: LitterOut | None = None
    tasks: list[TaskOut] = []


class ServiceOut(BaseModel):
    cycle: CycleOut
    mating_check: MatingCheckOut | None = Field(
        None, description="How inbred the litter would be, when the sire is on record."
    )
