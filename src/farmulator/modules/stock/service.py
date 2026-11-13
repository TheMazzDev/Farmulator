import uuid
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from farmulator.core.deps import RequestContext
from farmulator.core.errors import Conflict, Invalid
from farmulator.core.repo import get_or_404, get_ref
from farmulator.modules.farm.models import Enterprise, Location
from farmulator.modules.stock.models import (
    LotOrigin,
    MovementReason,
    Product,
    StockLot,
    StockMovement,
)
from farmulator.modules.stock.schemas import (
    AdjustmentIn,
    Allocation,
    CostTotal,
    IssueIn,
    IssueOut,
    OnHandRow,
    ProductCreate,
    ReceiptIn,
    ReceiptOut,
    StockLotOut,
    TransferIn,
    TransferOut,
)

CENT = Decimal("0.01")


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


# --- Products -----------------------------------------------------------------------


async def create_product(
    session: AsyncSession, ctx: RequestContext, data: ProductCreate
) -> Product:
    if data.id is not None and (existing := await session.get(Product, data.id)) is not None:
        return existing
    if await session.scalar(select(Product.id).where(Product.sku == data.sku)):
        raise Conflict(f"A product with SKU '{data.sku}' already exists.", code="duplicate_sku")
    product = Product(tenant_id=ctx.tenant_id, **data.model_dump(exclude_none=True))
    session.add(product)
    await session.flush()
    return product


async def list_products(session: AsyncSession) -> Sequence[Product]:
    return (await session.execute(select(Product).order_by(Product.name))).scalars().all()


# --- Ledger helpers -----------------------------------------------------------------


async def _replayed(session: AsyncSession, operation_id: uuid.UUID | None) -> list[StockMovement]:
    if operation_id is None:
        return []
    stmt = (
        select(StockMovement)
        .where(StockMovement.operation_id == operation_id)
        .order_by(StockMovement.created_at, StockMovement.id)
    )
    return list((await session.execute(stmt)).scalars().all())


def _movement(
    ctx: RequestContext,
    operation_id: uuid.UUID,
    lot: StockLot,
    quantity: Decimal,
    reason: MovementReason,
    occurred_at: datetime,
    *,
    enterprise_id: uuid.UUID | None = None,
    ref_type: str | None = None,
    ref_id: uuid.UUID | None = None,
    note: str | None = None,
) -> StockMovement:
    return StockMovement(
        tenant_id=ctx.tenant_id,
        operation_id=operation_id,
        lot_id=lot.id,
        product_id=lot.product_id,
        location_id=lot.location_id,
        quantity=quantity,
        unit_cost=lot.unit_cost,
        currency=lot.currency,
        reason=reason,
        enterprise_id=enterprise_id,
        ref_type=ref_type,
        ref_id=ref_id,
        occurred_at=occurred_at,
        recorded_by=ctx.user_id,
        note=note,
    )


async def _pick_lots(
    session: AsyncSession, product_id: uuid.UUID, location_id: uuid.UUID, on: datetime
) -> list[StockLot]:
    """Lots to draw from, locked: first to expire first, then oldest first.
    Expired lots are never picked automatically; they stay on hand for a write-off."""
    stmt = (
        select(StockLot)
        .where(
            StockLot.product_id == product_id,
            StockLot.location_id == location_id,
            StockLot.quantity_on_hand > 0,
            or_(StockLot.expiry_date.is_(None), StockLot.expiry_date >= on.date()),
        )
        .order_by(StockLot.expiry_date.asc().nulls_last(), StockLot.received_at, StockLot.id)
        .with_for_update()
    )
    return list((await session.execute(stmt)).scalars().all())


async def _draw(
    session: AsyncSession,
    ctx: RequestContext,
    operation_id: uuid.UUID,
    product_id: uuid.UUID,
    location_id: uuid.UUID,
    quantity: Decimal,
    reason: MovementReason,
    occurred_at: datetime,
    **movement_fields: object,
) -> list[tuple[StockLot, Decimal]]:
    lots = await _pick_lots(session, product_id, location_id, occurred_at)
    available = sum((lot.quantity_on_hand for lot in lots), Decimal(0))
    if available < quantity:
        raise Conflict(
            f"Only {available} in stock here (not counting expired lots); {quantity} needed.",
            code="insufficient_stock",
            available=str(available),
        )
    taken: list[tuple[StockLot, Decimal]] = []
    remaining = quantity
    for lot in lots:
        if remaining == 0:
            break
        take = min(remaining, lot.quantity_on_hand)
        lot.quantity_on_hand -= take
        remaining -= take
        session.add(
            _movement(ctx, operation_id, lot, -take, reason, occurred_at, **movement_fields)  # type: ignore[arg-type]
        )
        taken.append((lot, take))
    return taken


def _allocations(rows: list[tuple[StockLot, Decimal]]) -> list[Allocation]:
    return [
        Allocation(
            lot_id=lot.id,
            quantity=qty,
            unit_cost=lot.unit_cost,
            currency=lot.currency,
            cost=_money(qty * lot.unit_cost),
            batch_number=lot.batch_number,
            expiry_date=lot.expiry_date,
        )
        for lot, qty in rows
    ]


def _totals(allocations: list[Allocation]) -> list[CostTotal]:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for a in allocations:
        totals[a.currency] += a.cost
    return [CostTotal(currency=c, amount=amount) for c, amount in sorted(totals.items())]


async def _lots_for(
    session: AsyncSession, movements: list[StockMovement]
) -> dict[uuid.UUID, StockLot]:
    ids = {m.lot_id for m in movements}
    rows = (await session.execute(select(StockLot).where(StockLot.id.in_(ids)))).scalars()
    return {lot.id: lot for lot in rows}


# --- Operations ---------------------------------------------------------------------


async def receive(session: AsyncSession, ctx: RequestContext, data: ReceiptIn) -> ReceiptOut:
    if previous := await _replayed(session, data.operation_id):
        lot = await get_or_404(session, StockLot, previous[0].lot_id, "stock lot")
        return ReceiptOut(
            operation_id=previous[0].operation_id, lot=StockLotOut.model_validate(lot)
        )

    await get_ref(session, Product, data.product_id, "product")
    await get_ref(session, Location, data.location_id, "location")
    operation_id = data.operation_id or uuid.uuid4()
    opening = data.opening_balance
    lot = StockLot(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        product_id=data.product_id,
        location_id=data.location_id,
        batch_number=data.batch_number,
        expiry_date=data.expiry_date,
        received_at=data.occurred_at,
        unit_cost=data.unit_cost,
        currency=data.currency,
        quantity_on_hand=data.quantity,
        origin_type=LotOrigin.OPENING if opening else LotOrigin.PURCHASE,
        supplier_name=data.supplier_name,
    )
    session.add(lot)
    reason = MovementReason.OPENING if opening else MovementReason.RECEIPT
    session.add(_movement(ctx, operation_id, lot, data.quantity, reason, data.occurred_at))
    await session.flush()
    return ReceiptOut(operation_id=operation_id, lot=StockLotOut.model_validate(lot))


async def issue(session: AsyncSession, ctx: RequestContext, data: IssueIn) -> IssueOut:
    if previous := await _replayed(session, data.operation_id):
        lots = await _lots_for(session, previous)
        allocations = _allocations([(lots[m.lot_id], -m.quantity) for m in previous])
        return IssueOut(
            operation_id=previous[0].operation_id,
            product_id=previous[0].product_id,
            location_id=previous[0].location_id,
            quantity=sum((a.quantity for a in allocations), Decimal(0)),
            costs=_totals(allocations),
            allocations=allocations,
        )

    await get_ref(session, Product, data.product_id, "product")
    await get_ref(session, Location, data.location_id, "location")
    await get_ref(session, Enterprise, data.enterprise_id, "enterprise")
    operation_id = data.operation_id or uuid.uuid4()
    taken = await _draw(
        session,
        ctx,
        operation_id,
        data.product_id,
        data.location_id,
        data.quantity,
        MovementReason(data.reason),
        data.occurred_at,
        enterprise_id=data.enterprise_id,
        ref_type=data.ref_type,
        ref_id=data.ref_id,
        note=data.note,
    )
    await session.flush()
    allocations = _allocations(taken)
    return IssueOut(
        operation_id=operation_id,
        product_id=data.product_id,
        location_id=data.location_id,
        quantity=data.quantity,
        costs=_totals(allocations),
        allocations=allocations,
    )


async def transfer(session: AsyncSession, ctx: RequestContext, data: TransferIn) -> TransferOut:
    if previous := await _replayed(session, data.operation_id):
        outs = [m for m in previous if m.reason == MovementReason.TRANSFER_OUT]
        ins = [m for m in previous if m.reason == MovementReason.TRANSFER_IN]
        lots = await _lots_for(session, outs)
        return TransferOut(
            operation_id=previous[0].operation_id,
            allocations=_allocations([(lots[m.lot_id], -m.quantity) for m in outs]),
            new_lot_ids=[m.lot_id for m in ins],
        )

    if data.from_location_id == data.to_location_id:
        raise Invalid("Choose two different locations.", code="same_location")
    await get_ref(session, Product, data.product_id, "product")
    await get_ref(session, Location, data.from_location_id, "location")
    await get_ref(session, Location, data.to_location_id, "location")
    operation_id = data.operation_id or uuid.uuid4()
    taken = await _draw(
        session,
        ctx,
        operation_id,
        data.product_id,
        data.from_location_id,
        data.quantity,
        MovementReason.TRANSFER_OUT,
        data.occurred_at,
        note=data.note,
    )
    new_lot_ids: list[uuid.UUID] = []
    for source, qty in taken:
        # The new lot keeps the source's cost, batch and expiry, and points back to it.
        lot = StockLot(
            id=uuid.uuid4(),
            tenant_id=ctx.tenant_id,
            product_id=source.product_id,
            location_id=data.to_location_id,
            batch_number=source.batch_number,
            expiry_date=source.expiry_date,
            received_at=source.received_at,
            unit_cost=source.unit_cost,
            currency=source.currency,
            quantity_on_hand=qty,
            origin_type=LotOrigin.TRANSFER,
            origin_id=source.id,
            supplier_name=source.supplier_name,
        )
        session.add(lot)
        session.add(
            _movement(
                ctx,
                operation_id,
                lot,
                qty,
                MovementReason.TRANSFER_IN,
                data.occurred_at,
                note=data.note,
            )
        )
        new_lot_ids.append(lot.id)
    await session.flush()
    return TransferOut(
        operation_id=operation_id, allocations=_allocations(taken), new_lot_ids=new_lot_ids
    )


async def adjust(session: AsyncSession, ctx: RequestContext, data: AdjustmentIn) -> StockLot:
    if previous := await _replayed(session, data.operation_id):
        return await get_or_404(session, StockLot, previous[0].lot_id, "stock lot")
    stmt = select(StockLot).where(StockLot.id == data.lot_id).with_for_update()
    lot = (await session.execute(stmt)).scalar_one_or_none()
    if lot is None:
        raise Invalid(f"No stock lot with id {data.lot_id}.", code="stock_lot_not_found")
    new_quantity = lot.quantity_on_hand + data.quantity_delta
    if new_quantity < 0:
        raise Invalid(
            f"The lot has only {lot.quantity_on_hand}; it cannot go below zero.",
            code="adjustment_below_zero",
        )
    lot.quantity_on_hand = new_quantity
    session.add(
        _movement(
            ctx,
            data.operation_id or uuid.uuid4(),
            lot,
            data.quantity_delta,
            MovementReason.ADJUSTMENT,
            data.occurred_at,
            note=data.note,
        )
    )
    await session.flush()
    return lot


# --- Reports ------------------------------------------------------------------------


async def list_lots(
    session: AsyncSession,
    product_id: uuid.UUID | None,
    location_id: uuid.UUID | None,
    include_empty: bool,
) -> Sequence[StockLot]:
    stmt = select(StockLot)
    if product_id is not None:
        stmt = stmt.where(StockLot.product_id == product_id)
    if location_id is not None:
        stmt = stmt.where(StockLot.location_id == location_id)
    if not include_empty:
        stmt = stmt.where(StockLot.quantity_on_hand > 0)
    stmt = stmt.order_by(StockLot.expiry_date.asc().nulls_last(), StockLot.received_at)
    return (await session.execute(stmt)).scalars().all()


async def on_hand(
    session: AsyncSession, location_id: uuid.UUID | None, product_id: uuid.UUID | None
) -> list[OnHandRow]:
    quantity = func.sum(StockLot.quantity_on_hand)
    value = func.sum(StockLot.quantity_on_hand * StockLot.unit_cost)
    stmt = (
        select(
            Product.id,
            Product.sku,
            Product.name,
            Product.base_unit,
            Product.reorder_level,
            StockLot.location_id,
            StockLot.currency,
            quantity.label("quantity"),
            value.label("value"),
        )
        .join(Product, Product.id == StockLot.product_id)
        .where(StockLot.quantity_on_hand > 0)
        .group_by(Product.id, StockLot.location_id, StockLot.currency)
        .order_by(Product.name)
    )
    if location_id is not None:
        stmt = stmt.where(StockLot.location_id == location_id)
    if product_id is not None:
        stmt = stmt.where(StockLot.product_id == product_id)
    return [
        OnHandRow(
            product_id=row.id,
            sku=row.sku,
            name=row.name,
            base_unit=row.base_unit,
            location_id=row.location_id,
            currency=row.currency,
            quantity=row.quantity,
            value=_money(row.value),
            below_reorder_level=row.reorder_level is not None and row.quantity < row.reorder_level,
        )
        for row in (await session.execute(stmt)).all()
    ]
