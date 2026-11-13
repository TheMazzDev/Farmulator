import uuid

from fastapi import APIRouter

from farmulator.core.deps import Ctx, DbSession
from farmulator.modules.stock import service
from farmulator.modules.stock.schemas import (
    AdjustmentIn,
    IssueIn,
    IssueOut,
    OnHandRow,
    ProductCreate,
    ProductOut,
    ReceiptIn,
    ReceiptOut,
    StockLotOut,
    TransferIn,
    TransferOut,
)

router = APIRouter(prefix="/v1", tags=["stock"])


@router.post("/products", response_model=ProductOut, status_code=201)
async def create_product(body: ProductCreate, session: DbSession, ctx: Ctx) -> ProductOut:
    product = await service.create_product(session, ctx, body)
    await session.commit()
    return ProductOut.model_validate(product)


@router.get("/products", response_model=list[ProductOut])
async def list_products(session: DbSession) -> list[ProductOut]:
    return [ProductOut.model_validate(p) for p in await service.list_products(session)]


@router.post("/stock/receipts", response_model=ReceiptOut, status_code=201)
async def receive_stock(body: ReceiptIn, session: DbSession, ctx: Ctx) -> ReceiptOut:
    """Stock in from a supplier, or an opening balance."""
    result = await service.receive(session, ctx, body)
    await session.commit()
    return result


@router.post("/stock/issues", response_model=IssueOut, status_code=201)
async def issue_stock(body: IssueIn, session: DbSession, ctx: Ctx) -> IssueOut:
    """Stock out to feeding, treatment, dipping or field work, costed first-expiry-first."""
    result = await service.issue(session, ctx, body)
    await session.commit()
    return result


@router.post("/stock/transfers", response_model=TransferOut, status_code=201)
async def transfer_stock(body: TransferIn, session: DbSession, ctx: Ctx) -> TransferOut:
    result = await service.transfer(session, ctx, body)
    await session.commit()
    return result


@router.post("/stock/adjustments", response_model=StockLotOut, status_code=201)
async def adjust_stock(body: AdjustmentIn, session: DbSession, ctx: Ctx) -> StockLotOut:
    """Correct a lot after a count, a loss or damage. The reason is kept in the ledger."""
    lot = await service.adjust(session, ctx, body)
    await session.commit()
    return StockLotOut.model_validate(lot)


@router.get("/stock/lots", response_model=list[StockLotOut])
async def list_lots(
    session: DbSession,
    product_id: uuid.UUID | None = None,
    location_id: uuid.UUID | None = None,
    include_empty: bool = False,
) -> list[StockLotOut]:
    lots = await service.list_lots(session, product_id, location_id, include_empty)
    return [StockLotOut.model_validate(lot) for lot in lots]


@router.get("/stock/on-hand", response_model=list[OnHandRow])
async def stock_on_hand(
    session: DbSession,
    location_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
) -> list[OnHandRow]:
    """Quantity and value on hand per product, location and currency."""
    return await service.on_hand(session, location_id, product_id)
