import uuid
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Any

from sqlalchemy import Date, cast, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from farmulator.core.deps import RequestContext
from farmulator.core.errors import Conflict, Invalid
from farmulator.core.repo import get_or_404, get_ref, paginate
from farmulator.modules.farm.models import Enterprise, Farm, Location, Tenant
from farmulator.modules.livestock.models import (
    Animal,
    AnimalEvent,
    AnimalGroup,
    AnimalStatus,
    Breed,
    EventType,
    GroupStatus,
    Sex,
    Species,
)
from farmulator.modules.livestock.pedigree import (
    Parents,
    PedigreeCycleError,
    RelationshipCalculator,
)
from farmulator.modules.livestock.schemas import (
    AnimalCreate,
    AnimalEventCreate,
    AnimalUpdate,
    BreedCreate,
    GroupCreate,
    InbreedingOut,
    MatingCheckOut,
    PedigreeNode,
    SpeciesCreate,
)

COEFFICIENT_PLACES = Decimal("0.000001")

# --- Reference data -----------------------------------------------------------------


async def list_species(session: AsyncSession) -> Sequence[Species]:
    return (await session.execute(select(Species).order_by(Species.name))).scalars().all()


async def create_species(
    session: AsyncSession, ctx: RequestContext, data: SpeciesCreate
) -> Species:
    exists = await session.scalar(select(Species.id).where(Species.code == data.code))
    if exists:
        raise Conflict(f"A species with code '{data.code}' already exists.", code="duplicate_code")
    species = Species(tenant_id=ctx.tenant_id, **data.model_dump())
    session.add(species)
    await session.flush()
    return species


async def list_breeds(session: AsyncSession, species_id: uuid.UUID | None) -> Sequence[Breed]:
    stmt = select(Breed).order_by(Breed.name)
    if species_id is not None:
        stmt = stmt.where(Breed.species_id == species_id)
    return (await session.execute(stmt)).scalars().all()


async def create_breed(session: AsyncSession, ctx: RequestContext, data: BreedCreate) -> Breed:
    await get_ref(session, Species, data.species_id, "species")
    breed = Breed(tenant_id=ctx.tenant_id, **data.model_dump())
    session.add(breed)
    await session.flush()
    return breed


# --- Animals ------------------------------------------------------------------------


async def _check_parent(
    session: AsyncSession,
    parent_id: uuid.UUID | None,
    role: str,
    species_id: uuid.UUID,
    child_birth: Any,
) -> None:
    parent = await get_ref(session, Animal, parent_id, role)
    if parent is None:
        return
    expected = Sex.MALE if role == "sire" else Sex.FEMALE
    if parent.sex != expected:
        raise Invalid(f"The {role} must be {expected.value}.", code=f"{role}_wrong_sex")
    if parent.species_id != species_id:
        raise Invalid(f"The {role} is a different species.", code=f"{role}_wrong_species")
    if parent.birth_date and child_birth and parent.birth_date >= child_birth:
        raise Invalid(f"The {role} must be born before its offspring.", code=f"{role}_too_young")


async def _check_tag_free(
    session: AsyncSession, tag: str, exclude_id: uuid.UUID | None = None
) -> None:
    stmt = select(Animal.id).where(Animal.tag == tag, Animal.deleted_at.is_(None))
    if exclude_id is not None:
        stmt = stmt.where(Animal.id != exclude_id)
    if await session.scalar(stmt):
        raise Conflict(f"Tag '{tag}' is already used by another animal.", code="duplicate_tag")


async def create_animal(session: AsyncSession, ctx: RequestContext, data: AnimalCreate) -> Animal:
    if data.id is not None and (existing := await session.get(Animal, data.id)) is not None:
        return existing  # a retried create from an offline device
    species = await get_ref(session, Species, data.species_id, "species")
    assert species is not None
    await get_ref(session, Farm, data.farm_id, "farm")
    breed = await get_ref(session, Breed, data.breed_id, "breed")
    if breed is not None and breed.species_id != species.id:
        raise Invalid("That breed belongs to a different species.", code="breed_wrong_species")
    await get_ref(session, AnimalGroup, data.group_id, "animal group")
    await get_ref(session, Location, data.location_id, "location")
    await get_ref(session, Enterprise, data.enterprise_id, "enterprise")
    await _check_parent(session, data.sire_id, "sire", species.id, data.birth_date)
    await _check_parent(session, data.dam_id, "dam", species.id, data.birth_date)
    await _check_tag_free(session, data.tag)

    animal = Animal(tenant_id=ctx.tenant_id, **data.model_dump(exclude_none=True))
    session.add(animal)
    await session.flush()
    return animal


async def _is_descendant(session: AsyncSession, ancestor: uuid.UUID, candidate: uuid.UUID) -> bool:
    stmt = text(
        """
        WITH RECURSIVE descendants(id) AS (
            SELECT id FROM animal WHERE sire_id = :ancestor OR dam_id = :ancestor
            UNION
            SELECT a.id FROM animal a JOIN descendants d ON a.sire_id = d.id OR a.dam_id = d.id
        )
        SELECT EXISTS (SELECT 1 FROM descendants WHERE id = :candidate)
        """
    )
    return bool(await session.scalar(stmt, {"ancestor": ancestor, "candidate": candidate}))


async def update_animal(session: AsyncSession, animal_id: uuid.UUID, data: AnimalUpdate) -> Animal:
    animal = await get_or_404(session, Animal, animal_id, "animal")
    if animal.version != data.version:
        raise Conflict(
            "This animal was changed by someone else. Reload it and try again.",
            code="stale_version",
            current_version=animal.version,
        )
    changes = data.model_dump(exclude_unset=True, exclude={"version"})
    if "tag" in changes and changes["tag"] != animal.tag:
        await _check_tag_free(session, changes["tag"], exclude_id=animal.id)
    if changes.get("breed_id") is not None:
        breed = await get_ref(session, Breed, changes["breed_id"], "breed")
        if breed is not None and breed.species_id != animal.species_id:
            raise Invalid("That breed belongs to a different species.", code="breed_wrong_species")
    birth = changes.get("birth_date", animal.birth_date)
    for role in ("sire", "dam"):
        parent_id = changes.get(f"{role}_id")
        if parent_id is None:
            continue
        if parent_id == animal.id or await _is_descendant(session, animal.id, parent_id):
            raise Invalid(
                f"An animal's {role} cannot be the animal itself or one of its offspring.",
                code="pedigree_cycle",
            )
        await _check_parent(session, parent_id, role, animal.species_id, birth)
    await get_ref(session, Enterprise, changes.get("enterprise_id"), "enterprise")
    for field, value in changes.items():
        setattr(animal, field, value)
    try:
        await session.flush()
    except StaleDataError as exc:
        raise Conflict("This animal was changed by someone else.", code="stale_version") from exc
    return animal


async def list_animals(
    session: AsyncSession,
    *,
    farm_id: uuid.UUID | None,
    species_id: uuid.UUID | None,
    status: AnimalStatus | None,
    sex: Sex | None,
    tag: str | None,
    limit: int,
    cursor: str | None,
) -> tuple[list[Animal], str | None]:
    stmt = select(Animal).where(Animal.deleted_at.is_(None))
    if farm_id is not None:
        stmt = stmt.where(Animal.farm_id == farm_id)
    if species_id is not None:
        stmt = stmt.where(Animal.species_id == species_id)
    if status is not None:
        stmt = stmt.where(Animal.status == status)
    if sex is not None:
        stmt = stmt.where(Animal.sex == sex)
    if tag:
        stmt = stmt.where(Animal.tag.ilike(f"{tag}%"))
    return await paginate(session, stmt, Animal, limit, cursor)


# --- Groups -------------------------------------------------------------------------


async def create_group(
    session: AsyncSession, ctx: RequestContext, data: GroupCreate
) -> AnimalGroup:
    if data.id is not None and (existing := await session.get(AnimalGroup, data.id)) is not None:
        return existing
    await get_ref(session, Farm, data.farm_id, "farm")
    await get_ref(session, Species, data.species_id, "species")
    await get_ref(session, Breed, data.breed_id, "breed")
    await get_ref(session, Location, data.location_id, "location")
    await get_ref(session, Enterprise, data.enterprise_id, "enterprise")
    group = AnimalGroup(tenant_id=ctx.tenant_id, **data.model_dump(exclude_none=True))
    session.add(group)
    await session.flush()
    return group


async def list_groups(session: AsyncSession, farm_id: uuid.UUID | None) -> Sequence[AnimalGroup]:
    stmt = select(AnimalGroup).where(AnimalGroup.deleted_at.is_(None))
    if farm_id is not None:
        stmt = stmt.where(AnimalGroup.farm_id == farm_id)
    return (await session.execute(stmt.order_by(AnimalGroup.start_date.desc()))).scalars().all()


# --- Events -------------------------------------------------------------------------

REMOVING_EVENTS = {
    EventType.SALE: AnimalStatus.SOLD,
    EventType.DEATH: AnimalStatus.DEAD,
    EventType.SLAUGHTER: AnimalStatus.SLAUGHTERED,
}


async def _withdrawal_until(session: AsyncSession, animal_id: uuid.UUID, on: Any) -> Any:
    """The latest withdrawal date still running on ``on``, if any."""
    withdrawal = cast(AnimalEvent.payload["withdrawal_until"].astext, Date)
    stmt = select(withdrawal).where(
        AnimalEvent.animal_id == animal_id,
        AnimalEvent.type.in_([EventType.TREATMENT, EventType.VACCINATION]),
        AnimalEvent.payload.has_key("withdrawal_until"),
        withdrawal >= on,
    )
    return await session.scalar(stmt.order_by(withdrawal.desc()).limit(1))


async def record_event(
    session: AsyncSession, ctx: RequestContext, data: AnimalEventCreate
) -> AnimalEvent:
    if data.id is not None and (existing := await session.get(AnimalEvent, data.id)) is not None:
        return existing  # a retried upload from an offline device

    payload: dict[str, Any] = data.payload.model_dump(mode="json", exclude_none=True)
    await get_ref(session, Location, data.location_id, "location")

    if data.animal_id is not None:
        animal = await get_ref(session, Animal, data.animal_id, "animal")
        assert animal is not None
        await _apply_to_animal(session, animal, data, payload)
    else:
        group = await get_ref(session, AnimalGroup, data.group_id, "animal group")
        assert group is not None
        await _apply_to_group(session, group, data, payload)

    event = AnimalEvent(
        tenant_id=ctx.tenant_id,
        animal_id=data.animal_id,
        group_id=data.group_id,
        type=data.type,
        occurred_at=data.occurred_at,
        location_id=data.location_id,
        source=data.source,
        recorded_by=ctx.user_id,
        payload=payload,
    )
    if data.id is not None:
        event.id = data.id
    session.add(event)
    await session.flush()
    return event


async def _apply_to_animal(
    session: AsyncSession, animal: Animal, data: Any, payload: dict[str, Any]
) -> None:
    changes_status = data.type in REMOVING_EVENTS or data.type == EventType.MOVEMENT
    if changes_status and animal.status != AnimalStatus.ACTIVE:
        raise Conflict(
            f"Animal {animal.tag} is already {animal.status.value}.", code="animal_not_active"
        )
    if data.type in (EventType.SALE, EventType.SLAUGHTER):
        if payload.get("count", 1) != 1:
            raise Invalid("For one animal, count must be 1.", code="count_for_individual")
        until = await _withdrawal_until(session, animal.id, data.occurred_at.date())
        if until is not None:
            if not data.override_withdrawal:
                raise Conflict(
                    f"Animal {animal.tag} is in a drug withdrawal period until {until}.",
                    code="withdrawal_period",
                    withdrawal_until=until.isoformat(),
                )
            payload["withdrawal_override"] = {
                "until": until.isoformat(),
                "reason": data.override_reason,
            }
    if data.type in REMOVING_EVENTS:
        animal.status = REMOVING_EVENTS[data.type]
    elif data.type == EventType.MOVEMENT:
        destination = await get_ref(session, Location, data.payload.to_location_id, "location")
        assert destination is not None
        animal.location_id = destination.id


async def _apply_to_group(
    session: AsyncSession, group: AnimalGroup, data: Any, payload: dict[str, Any]
) -> None:
    if group.status != GroupStatus.ACTIVE:
        raise Conflict(f"Group {group.name} is closed.", code="group_closed")
    if data.type in REMOVING_EVENTS:
        count = int(payload.get("count", 1))
        if count > group.head_count:
            raise Invalid(
                f"Group {group.name} has only {group.head_count} head.", code="count_too_high"
            )
        group.head_count -= count
    elif data.type == EventType.MOVEMENT:
        destination = await get_ref(session, Location, data.payload.to_location_id, "location")
        assert destination is not None
        group.location_id = destination.id


async def list_events(
    session: AsyncSession, animal_id: uuid.UUID, limit: int, cursor: str | None
) -> tuple[list[AnimalEvent], str | None]:
    await get_or_404(session, Animal, animal_id, "animal")
    stmt = select(AnimalEvent).where(AnimalEvent.animal_id == animal_id)
    return await paginate(session, stmt, AnimalEvent, limit, cursor)


# --- Pedigree -----------------------------------------------------------------------


async def load_ancestry(
    session: AsyncSession, start_ids: list[uuid.UUID], generations: int
) -> dict[uuid.UUID, Parents]:
    """Animals in the given animals' pedigrees, up to ``generations`` back. Parents of
    animals at the last generation are left out, so they count as unknown."""
    stmt = text(
        """
        WITH RECURSIVE ancestry(id, sire_id, dam_id, depth) AS (
            SELECT id, sire_id, dam_id, 0 FROM animal WHERE id = ANY(:start_ids)
            UNION
            SELECT a.id, a.sire_id, a.dam_id, anc.depth + 1
            FROM animal a
            JOIN ancestry anc ON a.id = anc.sire_id OR a.id = anc.dam_id
            WHERE anc.depth < :generations
        )
        SELECT id, sire_id, dam_id, min(depth) AS depth
        FROM ancestry GROUP BY id, sire_id, dam_id
        """
    )
    rows = (await session.execute(stmt, {"start_ids": start_ids, "generations": generations})).all()
    return {
        row.id: (None, None) if row.depth >= generations else (row.sire_id, row.dam_id)
        for row in rows
    }


def _as_decimal(value: Fraction) -> Decimal:
    exact = Decimal(value.numerator) / Decimal(value.denominator)
    return exact.quantize(COEFFICIENT_PLACES, rounding=ROUND_HALF_UP)


def _calculator(parents: dict[uuid.UUID, Parents]) -> RelationshipCalculator:
    try:
        return RelationshipCalculator(parents)
    except PedigreeCycleError as exc:
        raise Conflict(str(exc), code="pedigree_cycle") from exc


async def inbreeding(
    session: AsyncSession, animal_id: uuid.UUID, generations: int
) -> InbreedingOut:
    await get_or_404(session, Animal, animal_id, "animal")
    # The animal's parents are generation one, so ancestry reaches `generations` deep.
    parents = await load_ancestry(session, [animal_id], generations)
    coefficient = _calculator(parents).inbreeding(animal_id)
    return InbreedingOut(
        animal_id=animal_id,
        inbreeding_coefficient=_as_decimal(coefficient),
        generations_checked=generations,
    )


async def mating_check(
    session: AsyncSession,
    ctx: RequestContext,
    sire_id: uuid.UUID,
    dam_id: uuid.UUID,
    generations: int,
) -> MatingCheckOut:
    sire = await get_ref(session, Animal, sire_id, "sire")
    dam = await get_ref(session, Animal, dam_id, "dam")
    assert sire is not None and dam is not None
    if sire.sex != Sex.MALE:
        raise Invalid("The sire must be male.", code="sire_wrong_sex")
    if dam.sex != Sex.FEMALE:
        raise Invalid("The dam must be female.", code="dam_wrong_sex")
    if sire.species_id != dam.species_id:
        raise Invalid("The sire and dam are different species.", code="different_species")
    tenant = await get_or_404(session, Tenant, ctx.tenant_id, "tenant")

    # The sire and dam are the offspring's first generation.
    parents = await load_ancestry(session, [sire_id, dam_id], generations - 1)
    calc = _calculator(parents)
    coefficient = _as_decimal(calc.offspring_inbreeding(sire_id, dam_id))
    return MatingCheckOut(
        sire_id=sire_id,
        dam_id=dam_id,
        offspring_inbreeding=coefficient,
        limit=tenant.inbreeding_limit,
        exceeds_limit=coefficient > tenant.inbreeding_limit,
        common_ancestor_ids=sorted(calc.common_ancestors(sire_id, dam_id), key=str),
        generations_checked=generations,
    )


async def pedigree_tree(
    session: AsyncSession, animal_id: uuid.UUID, generations: int
) -> PedigreeNode:
    await get_or_404(session, Animal, animal_id, "animal")
    parents = await load_ancestry(session, [animal_id], generations)
    animals = {
        a.id: a
        for a in (await session.execute(select(Animal).where(Animal.id.in_(parents)))).scalars()
    }

    def build(node_id: uuid.UUID | None, depth: int) -> PedigreeNode | None:
        if node_id is None or node_id not in animals:
            return None
        a = animals[node_id]
        sire, dam = parents.get(node_id, (None, None))
        return PedigreeNode(
            id=a.id,
            tag=a.tag,
            name=a.name,
            sex=a.sex,
            breed_id=a.breed_id,
            is_external=a.is_external,
            sire=build(sire, depth + 1) if depth < generations else None,
            dam=build(dam, depth + 1) if depth < generations else None,
        )

    tree = build(animal_id, 0)
    assert tree is not None
    return tree
