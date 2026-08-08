"""Create FastAPI application."""

from __future__ import annotations

from fastapi import FastAPI

from novel_agent import __version__
from novel_agent.api.errors import register_exception_handlers
from novel_agent.api.routers import router as v1_router
from novel_agent.bootstrap import ensure_data_dirs
from novel_agent.infrastructure.llm.gateway import LLMGateway
from novel_agent.infrastructure.persistence.db import create_all_tables, get_engine, get_session_factory
from novel_agent.settings import Settings, get_settings


def create_app(settings: Settings | None = None, *, database_url: str | None = None) -> FastAPI:
    settings = ensure_data_dirs(settings or get_settings())
    engine = get_engine(settings, url=database_url)
    create_all_tables(engine)
    factory = get_session_factory(settings, url=database_url)

    app = FastAPI(
        title="Novel-Agent",
        version=__version__,
        description="Phase 1 MVP: book creation flow via /api/v1",
    )
    app.state.settings = settings
    app.state.session_factory = factory
    app.state.gateway = LLMGateway(settings=settings)
    register_exception_handlers(app)
    app.include_router(v1_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__, "phase": "1"}

    return app
