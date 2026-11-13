"""Request context and the tenant-scoped database session.

Sign-in is a development stand-in: the caller names its farm business with the
X-Tenant-ID header. Phone and one-time-PIN sign-in with tokens replaces it in the
next module; nothing below the context changes when it does.
"""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from farmulator.core.config import Settings, get_settings
from farmulator.core.db import bind_tenant
from farmulator.core.errors import Unauthorized


@dataclass(frozen=True, slots=True)
class RequestContext:
    tenant_id: uuid.UUID
    user_id: uuid.UUID | None


def request_settings(request: Request) -> Settings:
    settings: Settings = getattr(request.app.state, "settings", None) or get_settings()
    return settings


async def get_context(
    request: Request,
    x_tenant_id: Annotated[str | None, Header()] = None,
    x_user_id: Annotated[str | None, Header()] = None,
) -> RequestContext:
    if not request_settings(request).dev_auth_enabled:
        raise Unauthorized("Sign-in is required.", code="sign_in_required")
    if not x_tenant_id:
        raise Unauthorized("Send your farm business ID in the X-Tenant-ID header.")
    try:
        tenant_id = uuid.UUID(x_tenant_id)
        user_id = uuid.UUID(x_user_id) if x_user_id else None
    except ValueError as exc:
        raise Unauthorized("X-Tenant-ID and X-User-ID must be UUIDs.") from exc
    return RequestContext(tenant_id=tenant_id, user_id=user_id)


async def get_session(
    request: Request, ctx: Annotated[RequestContext, Depends(get_context)]
) -> AsyncIterator[AsyncSession]:
    """A session bound to the caller's tenant. Handlers commit explicitly."""
    async with request.app.state.sessionmaker() as session:
        bind_tenant(session, ctx.tenant_id)
        yield session


Ctx = Annotated[RequestContext, Depends(get_context)]
DbSession = Annotated[AsyncSession, Depends(get_session)]
