"""Database engine and sessions, with the tenant set on every transaction.

Row-level security policies compare each row's ``tenant_id`` with the
``app.tenant_id`` setting. A session carries its tenant in ``session.info``, and
every transaction it begins first runs ``set_config('app.tenant_id', …, true)``.
The setting is transaction-local, so a pooled connection never leaks one farm's
tenant to the next request.
"""

import uuid
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, SessionTransaction

from farmulator.core.config import Settings

TENANT_KEY = "tenant_id"


def make_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        pool_pre_ping=True,
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: handlers build their response after committing.
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=True)


@event.listens_for(Session, "after_begin")
def _set_tenant_on_begin(
    session: Session, transaction: SessionTransaction, connection: Connection
) -> None:
    tenant_id: Any = session.info.get(TENANT_KEY)
    if tenant_id is not None:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )


def bind_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Scope a session to one tenant. Call before its first query."""
    if session.in_transaction():
        raise RuntimeError("bind_tenant must be called before the session's first query")
    session.info[TENANT_KEY] = tenant_id
