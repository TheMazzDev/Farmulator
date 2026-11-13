import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from farmulator.core.deps import RequestContext
from farmulator.core.errors import Conflict, Invalid
from farmulator.core.repo import get_or_404, get_ref
from farmulator.modules.farm.models import Enterprise, Farm, Location, Tenant
from farmulator.modules.farm.schemas import (
    EnterpriseCreate,
    FarmCreate,
    FarmUpdate,
    LocationCreate,
    TenantCreate,
)
from farmulator.modules.livestock.models import Species
from farmulator.modules.livestock.reference import DEFAULT_SPECIES


async def create_tenant(session: AsyncSession, data: TenantCreate, tenant_id: uuid.UUID) -> Tenant:
    """Create a farm business and seed its editable reference data.

    The session must already be bound to ``tenant_id``: row-level security only lets
    a session insert the tenant it is scoped to.
    """
    tenant = Tenant(
        id=tenant_id, name=data.name, country=data.country, base_currency=data.base_currency
    )
    session.add(tenant)
    await session.flush()
    for spec in DEFAULT_SPECIES:
        session.add(Species(tenant_id=tenant_id, **spec))
    await session.flush()
    return tenant


async def create_farm(session: AsyncSession, ctx: RequestContext, data: FarmCreate) -> Farm:
    if data.id is not None and (existing := await session.get(Farm, data.id)) is not None:
        return existing  # a retried create from an offline device
    farm = Farm(tenant_id=ctx.tenant_id, **data.model_dump(exclude_none=True))
    session.add(farm)
    await session.flush()
    return farm


async def list_farms(session: AsyncSession) -> Sequence[Farm]:
    stmt = select(Farm).where(Farm.deleted_at.is_(None)).order_by(Farm.name)
    return (await session.execute(stmt)).scalars().all()


async def update_farm(session: AsyncSession, farm_id: uuid.UUID, data: FarmUpdate) -> Farm:
    farm = await get_or_404(session, Farm, farm_id, "farm")
    if farm.version != data.version:
        raise Conflict(
            "This farm was changed by someone else. Reload it and try again.",
            code="stale_version",
            current_version=farm.version,
        )
    for field, value in data.model_dump(exclude_unset=True, exclude={"version"}).items():
        setattr(farm, field, value)
    try:
        await session.flush()
    except StaleDataError as exc:
        raise Conflict("This farm was changed by someone else.", code="stale_version") from exc
    return farm


async def create_enterprise(
    session: AsyncSession, ctx: RequestContext, data: EnterpriseCreate
) -> Enterprise:
    if data.id is not None and (existing := await session.get(Enterprise, data.id)) is not None:
        return existing
    await get_ref(session, Enterprise, data.parent_id, "parent enterprise")
    await get_ref(session, Species, data.species_id, "species")
    enterprise = Enterprise(tenant_id=ctx.tenant_id, **data.model_dump(exclude_none=True))
    session.add(enterprise)
    await session.flush()
    return enterprise


async def list_enterprises(session: AsyncSession) -> Sequence[Enterprise]:
    stmt = select(Enterprise).where(Enterprise.deleted_at.is_(None)).order_by(Enterprise.name)
    return (await session.execute(stmt)).scalars().all()


async def create_location(
    session: AsyncSession, ctx: RequestContext, data: LocationCreate
) -> Location:
    if data.id is not None and (existing := await session.get(Location, data.id)) is not None:
        return existing
    await get_ref(session, Farm, data.farm_id, "farm")
    parent = await get_ref(session, Location, data.parent_id, "parent location")
    if parent is not None and parent.farm_id != data.farm_id:
        raise Invalid("A location's parent must be on the same farm.", code="parent_other_farm")
    await get_ref(session, Enterprise, data.enterprise_id, "enterprise")
    location = Location(tenant_id=ctx.tenant_id, **data.model_dump(exclude_none=True))
    session.add(location)
    await session.flush()
    return location


async def list_locations(
    session: AsyncSession, farm_id: uuid.UUID | None = None
) -> Sequence[Location]:
    stmt = select(Location).where(Location.deleted_at.is_(None))
    if farm_id is not None:
        stmt = stmt.where(Location.farm_id == farm_id)
    return (await session.execute(stmt.order_by(Location.name))).scalars().all()
