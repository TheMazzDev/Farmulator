import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Query

from farmulator.core.deps import Ctx, DbSession
from farmulator.core.repo import Page, get_or_404
from farmulator.modules.livestock import service
from farmulator.modules.livestock.models import Animal, AnimalStatus, Sex
from farmulator.modules.livestock.schemas import (
    AnimalCreate,
    AnimalEventCreate,
    AnimalEventOut,
    AnimalOut,
    AnimalUpdate,
    BreedCreate,
    BreedOut,
    GroupCreate,
    GroupOut,
    InbreedingOut,
    MatingCheckIn,
    MatingCheckOut,
    PedigreeNode,
    SpeciesCreate,
    SpeciesOut,
)

router = APIRouter(prefix="/v1", tags=["livestock"])

Limit = Annotated[int, Query(ge=1, le=200)]
Generations = Annotated[int, Query(ge=1, le=10)]


@router.get("/species", response_model=list[SpeciesOut])
async def list_species(session: DbSession) -> list[SpeciesOut]:
    return [SpeciesOut.model_validate(s) for s in await service.list_species(session)]


@router.post("/species", response_model=SpeciesOut, status_code=201)
async def create_species(body: SpeciesCreate, session: DbSession, ctx: Ctx) -> SpeciesOut:
    species = await service.create_species(session, ctx, body)
    await session.commit()
    return SpeciesOut.model_validate(species)


@router.get("/breeds", response_model=list[BreedOut])
async def list_breeds(session: DbSession, species_id: uuid.UUID | None = None) -> list[BreedOut]:
    return [BreedOut.model_validate(b) for b in await service.list_breeds(session, species_id)]


@router.post("/breeds", response_model=BreedOut, status_code=201)
async def create_breed(body: BreedCreate, session: DbSession, ctx: Ctx) -> BreedOut:
    breed = await service.create_breed(session, ctx, body)
    await session.commit()
    return BreedOut.model_validate(breed)


@router.post("/animals", response_model=AnimalOut, status_code=201)
async def create_animal(body: AnimalCreate, session: DbSession, ctx: Ctx) -> AnimalOut:
    animal = await service.create_animal(session, ctx, body)
    await session.commit()
    return AnimalOut.model_validate(animal)


@router.get("/animals", response_model=Page[AnimalOut])
async def list_animals(
    session: DbSession,
    farm_id: uuid.UUID | None = None,
    species_id: uuid.UUID | None = None,
    status: AnimalStatus | None = None,
    sex: Sex | None = None,
    tag: Annotated[str | None, Query(description="Tag starts with")] = None,
    limit: Limit = 50,
    cursor: str | None = None,
) -> Page[AnimalOut]:
    rows, next_cursor = await service.list_animals(
        session,
        farm_id=farm_id,
        species_id=species_id,
        status=status,
        sex=sex,
        tag=tag,
        limit=limit,
        cursor=cursor,
    )
    return Page(items=[AnimalOut.model_validate(a) for a in rows], next_cursor=next_cursor)


@router.get("/animals/{animal_id}", response_model=AnimalOut)
async def get_animal(animal_id: uuid.UUID, session: DbSession) -> AnimalOut:
    return AnimalOut.model_validate(await get_or_404(session, Animal, animal_id, "animal"))


@router.patch("/animals/{animal_id}", response_model=AnimalOut)
async def update_animal(animal_id: uuid.UUID, body: AnimalUpdate, session: DbSession) -> AnimalOut:
    animal = await service.update_animal(session, animal_id, body)
    await session.commit()
    return AnimalOut.model_validate(animal)


@router.get("/animals/{animal_id}/events", response_model=Page[AnimalEventOut])
async def list_animal_events(
    animal_id: uuid.UUID, session: DbSession, limit: Limit = 50, cursor: str | None = None
) -> Page[AnimalEventOut]:
    rows, next_cursor = await service.list_events(session, animal_id, limit, cursor)
    return Page(items=[AnimalEventOut.model_validate(e) for e in rows], next_cursor=next_cursor)


@router.get("/animals/{animal_id}/pedigree", response_model=PedigreeNode)
async def get_pedigree(
    animal_id: uuid.UUID, session: DbSession, generations: Generations = 5
) -> PedigreeNode:
    return await service.pedigree_tree(session, animal_id, generations)


@router.get("/animals/{animal_id}/inbreeding", response_model=InbreedingOut)
async def get_inbreeding(
    animal_id: uuid.UUID, session: DbSession, generations: Generations = 5
) -> InbreedingOut:
    return await service.inbreeding(session, animal_id, generations)


@router.post("/animal-groups", response_model=GroupOut, status_code=201)
async def create_group(body: GroupCreate, session: DbSession, ctx: Ctx) -> GroupOut:
    group = await service.create_group(session, ctx, body)
    await session.commit()
    return GroupOut.model_validate(group)


@router.get("/animal-groups", response_model=list[GroupOut])
async def list_groups(session: DbSession, farm_id: uuid.UUID | None = None) -> list[GroupOut]:
    return [GroupOut.model_validate(g) for g in await service.list_groups(session, farm_id)]


@router.post("/animal-events", response_model=AnimalEventOut, status_code=201)
async def record_event(
    body: Annotated[AnimalEventCreate, Body()], session: DbSession, ctx: Ctx
) -> AnimalEventOut:
    event = await service.record_event(session, ctx, body)
    await session.commit()
    return AnimalEventOut.model_validate(event)


@router.post("/breeding/mating-check", response_model=MatingCheckOut)
async def mating_check(body: MatingCheckIn, session: DbSession, ctx: Ctx) -> MatingCheckOut:
    """How inbred the offspring of this pair would be, against the farm's limit."""
    return await service.mating_check(session, ctx, body.sire_id, body.dam_id, body.generations)
