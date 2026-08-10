"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker

from novel_agent.infrastructure.llm.gateway import LLMGateway
from novel_agent.infrastructure.persistence.db import get_session_factory
from novel_agent.settings import Settings, get_settings


def get_app_settings() -> Settings:
    return get_settings()


def get_db(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] = getattr(request.app.state, "session_factory", None) or get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_gateway(request: Request) -> LLMGateway:
    gw = getattr(request.app.state, "gateway", None)
    if gw is not None:
        assert isinstance(gw, LLMGateway)
        return gw
    return LLMGateway(settings=get_settings())
