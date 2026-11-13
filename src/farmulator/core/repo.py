"""Small query helpers shared by the modules."""

import base64
import json
import uuid
from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel
from sqlalchemy import Select, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from farmulator.core.errors import Invalid, NotFound

M = TypeVar("M")
T = TypeVar("T")


async def get_or_404(session: AsyncSession, model: type[M], obj_id: uuid.UUID, label: str) -> M:
    """Fetch a row by id. Row-level security already hides other tenants' rows."""
    stmt = select(model).where(model.id == obj_id)  # type: ignore[attr-defined]
    deleted_at = getattr(model, "deleted_at", None)  # only soft-deletable tables have it
    if deleted_at is not None:
        stmt = stmt.where(deleted_at.is_(None))
    obj = (await session.execute(stmt)).scalar_one_or_none()
    if obj is None:
        raise NotFound(f"No {label} with id {obj_id}.", code=f"{label.replace(' ', '_')}_not_found")
    return obj


async def get_ref(
    session: AsyncSession, model: type[M], obj_id: uuid.UUID | None, label: str
) -> M | None:
    """Like get_or_404 for a reference inside a request body: a missing one is a 422."""
    if obj_id is None:
        return None
    try:
        return await get_or_404(session, model, obj_id, label)
    except NotFound as exc:
        raise Invalid(exc.detail, code=exc.code) from exc


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = None


def _encode_cursor(created_at: datetime, obj_id: uuid.UUID) -> str:
    raw = json.dumps([created_at.isoformat(), str(obj_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        created, obj_id = json.loads(base64.urlsafe_b64decode(padded))
        return datetime.fromisoformat(created), uuid.UUID(obj_id)
    except (ValueError, TypeError) as exc:
        raise Invalid("The cursor is not valid.", code="bad_cursor") from exc


async def paginate(
    session: AsyncSession, stmt: Select[Any], model: Any, limit: int, cursor: str | None
) -> tuple[list[Any], str | None]:
    """Keyset pagination on (created_at, id): stable while new rows arrive."""
    if cursor:
        created, obj_id = _decode_cursor(cursor)
        stmt = stmt.where(tuple_(model.created_at, model.id) > tuple_(created, obj_id))
    stmt = stmt.order_by(model.created_at, model.id).limit(limit + 1)
    rows = list((await session.execute(stmt)).scalars().all())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = _encode_cursor(last.created_at, last.id)
    return rows, next_cursor
