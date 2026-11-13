"""Command line helpers.

farmulator seed-demo    Create a demo pig farm and print its X-Tenant-ID.
"""

import argparse
import asyncio
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select

import farmulator.models  # noqa: F401
from farmulator.core.config import get_settings
from farmulator.core.db import bind_tenant, make_engine, make_sessionmaker
from farmulator.core.deps import RequestContext
from farmulator.modules.farm import service as farm_service
from farmulator.modules.farm.models import EnterpriseKind, LocationKind
from farmulator.modules.farm.schemas import (
    EnterpriseCreate,
    FarmCreate,
    LocationCreate,
    TenantCreate,
)
from farmulator.modules.livestock import service as livestock_service
from farmulator.modules.livestock.models import Sex, Species
from farmulator.modules.livestock.schemas import AnimalCreate
from farmulator.modules.stock import service as stock_service
from farmulator.modules.stock.models import BaseUnit, ProductCategory
from farmulator.modules.stock.schemas import ProductCreate, ReceiptIn


async def seed_demo() -> uuid.UUID:
    settings = get_settings()
    engine = make_engine(settings)
    tenant_id = uuid.uuid4()
    ctx = RequestContext(tenant_id=tenant_id, user_id=None)
    try:
        async with make_sessionmaker(engine)() as session:
            bind_tenant(session, tenant_id)
            await farm_service.create_tenant(
                session,
                TenantCreate(name="Demo Piggery", country="ZW", base_currency="USD"),
                tenant_id,
            )
            farm = await farm_service.create_farm(session, ctx, FarmCreate(name="Mazowe Farm"))
            sows = await farm_service.create_enterprise(
                session, ctx, EnterpriseCreate(name="Breeding sows", kind=EnterpriseKind.LIVESTOCK)
            )
            house = await farm_service.create_location(
                session,
                ctx,
                LocationCreate(
                    farm_id=farm.id,
                    kind=LocationKind.PIG_HOUSE,
                    name="Farrowing house",
                    enterprise_id=sows.id,
                ),
            )
            store = await farm_service.create_location(
                session,
                ctx,
                LocationCreate(farm_id=farm.id, kind=LocationKind.FEED_STORE, name="Feed store"),
            )
            pig = (await session.execute(select(Species).where(Species.code == "pig"))).scalar_one()
            boar = await livestock_service.create_animal(
                session,
                ctx,
                AnimalCreate(
                    farm_id=farm.id,
                    species_id=pig.id,
                    tag="B-001",
                    name="Duke",
                    sex=Sex.MALE,
                    birth_date=date(2024, 3, 1),
                    location_id=house.id,
                    enterprise_id=sows.id,
                ),
            )
            for n in range(1, 4):
                await livestock_service.create_animal(
                    session,
                    ctx,
                    AnimalCreate(
                        farm_id=farm.id,
                        species_id=pig.id,
                        tag=f"S-00{n}",
                        sex=Sex.FEMALE,
                        birth_date=date(2024, 6, n),
                        location_id=house.id,
                        enterprise_id=sows.id,
                    ),
                )
            feed = await stock_service.create_product(
                session,
                ctx,
                ProductCreate(
                    sku="FEED-SOW-LAC",
                    name="Sow lactation meal",
                    category=ProductCategory.FEED,
                    base_unit=BaseUnit.KG,
                    reorder_level=Decimal("500"),
                ),
            )
            await stock_service.receive(
                session,
                ctx,
                ReceiptIn(
                    product_id=feed.id,
                    location_id=store.id,
                    quantity=Decimal("1000"),
                    unit_cost=Decimal("0.42"),
                    currency="USD",
                    opening_balance=True,
                    occurred_at=datetime.now(UTC),
                ),
            )
            await session.commit()
            print(f"Demo farm created. Boar {boar.tag} and 3 sows, 1,000 kg of sow meal.")
    finally:
        await engine.dispose()
    return tenant_id


def main() -> None:
    parser = argparse.ArgumentParser(prog="farmulator")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed-demo", help="Create a demo pig farm and print its tenant id.")
    args = parser.parse_args()
    if args.command == "seed-demo":
        tenant_id = asyncio.run(seed_demo())
        print(f"Use this header in requests:  X-Tenant-ID: {tenant_id}")


if __name__ == "__main__":
    main()
