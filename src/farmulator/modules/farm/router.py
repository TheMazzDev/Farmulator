import uuid

from fastapi import APIRouter, Request, status

from farmulator.core.db import bind_tenant
from farmulator.core.deps import Ctx, DbSession, request_settings
from farmulator.core.errors import NotFound
from farmulator.core.repo import get_or_404
from farmulator.modules.farm import service
from farmulator.modules.farm.models import Farm, Tenant
from farmulator.modules.farm.schemas import (
    EnterpriseCreate,
    EnterpriseOut,
    FarmCreate,
    FarmOut,
    FarmUpdate,
    LocationCreate,
    LocationOut,
    TenantCreate,
    TenantOut,
)

router = APIRouter(prefix="/v1")


@router.post(
    "/dev/tenants",
    response_model=TenantOut,
    status_code=status.HTTP_201_CREATED,
    tags=["development"],
    summary="Create a farm business (development only)",
)
async def create_tenant_dev(request: Request, body: TenantCreate) -> TenantOut:
    """Stands in for sign-up until phone sign-in exists. Disabled when dev auth is off."""
    if not request_settings(request).dev_auth_enabled:
        raise NotFound("Not available.")
    tenant_id = uuid.uuid4()
    async with request.app.state.sessionmaker() as session:
        bind_tenant(session, tenant_id)
        tenant = await service.create_tenant(session, body, tenant_id)
        await session.commit()
    return TenantOut.model_validate(tenant)


@router.get("/tenant", response_model=TenantOut, tags=["farm"], summary="Your farm business")
async def get_tenant(session: DbSession, ctx: Ctx) -> TenantOut:
    return TenantOut.model_validate(await get_or_404(session, Tenant, ctx.tenant_id, "tenant"))


@router.post("/farms", response_model=FarmOut, status_code=201, tags=["farm"])
async def create_farm(body: FarmCreate, session: DbSession, ctx: Ctx) -> FarmOut:
    farm = await service.create_farm(session, ctx, body)
    await session.commit()
    return FarmOut.model_validate(farm)


@router.get("/farms", response_model=list[FarmOut], tags=["farm"])
async def list_farms(session: DbSession) -> list[FarmOut]:
    return [FarmOut.model_validate(f) for f in await service.list_farms(session)]


@router.get("/farms/{farm_id}", response_model=FarmOut, tags=["farm"])
async def get_farm(farm_id: uuid.UUID, session: DbSession) -> FarmOut:
    return FarmOut.model_validate(await get_or_404(session, Farm, farm_id, "farm"))


@router.patch("/farms/{farm_id}", response_model=FarmOut, tags=["farm"])
async def update_farm(farm_id: uuid.UUID, body: FarmUpdate, session: DbSession) -> FarmOut:
    farm = await service.update_farm(session, farm_id, body)
    await session.commit()
    return FarmOut.model_validate(farm)


@router.post("/enterprises", response_model=EnterpriseOut, status_code=201, tags=["farm"])
async def create_enterprise(body: EnterpriseCreate, session: DbSession, ctx: Ctx) -> EnterpriseOut:
    enterprise = await service.create_enterprise(session, ctx, body)
    await session.commit()
    return EnterpriseOut.model_validate(enterprise)


@router.get("/enterprises", response_model=list[EnterpriseOut], tags=["farm"])
async def list_enterprises(session: DbSession) -> list[EnterpriseOut]:
    return [EnterpriseOut.model_validate(e) for e in await service.list_enterprises(session)]


@router.post("/locations", response_model=LocationOut, status_code=201, tags=["farm"])
async def create_location(body: LocationCreate, session: DbSession, ctx: Ctx) -> LocationOut:
    location = await service.create_location(session, ctx, body)
    await session.commit()
    return LocationOut.model_validate(location)


@router.get("/locations", response_model=list[LocationOut], tags=["farm"])
async def list_locations(session: DbSession, farm_id: uuid.UUID | None = None) -> list[LocationOut]:
    return [LocationOut.model_validate(x) for x in await service.list_locations(session, farm_id)]
