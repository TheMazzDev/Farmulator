"""The Farmulator API: one FastAPI app made of modules with clear boundaries."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from sqlalchemy import text

import farmulator.models  # noqa: F401  (registers every table)
from farmulator import __version__
from farmulator.core.config import Settings, get_settings
from farmulator.core.db import make_engine, make_sessionmaker
from farmulator.core.errors import install_error_handlers, problem
from farmulator.modules.farm.router import router as farm_router
from farmulator.modules.livestock.router import router as livestock_router
from farmulator.modules.stock.router import router as stock_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = make_engine(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await engine.dispose()

    app = FastAPI(
        title="Farmulator API",
        version=__version__,
        summary="Farm records that tell you if you're making money.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = make_sessionmaker(engine)

    install_error_handlers(app)
    app.include_router(farm_router)
    app.include_router(livestock_router)
    app.include_router(stock_router)

    @app.get("/health", tags=["system"])
    async def health(request: Request) -> object:
        try:
            async with request.app.state.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception:
            return problem(503, "Database unavailable", None, "database_unavailable")
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
