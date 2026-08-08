"""Unit of Work wrapper around a SQLAlchemy session."""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.orm import Session, sessionmaker

from novel_agent.infrastructure.persistence.db import get_session_factory
from novel_agent.settings import Settings


class UnitOfWork:
    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
        settings: Settings | None = None,
        *,
        url: str | None = None,
    ) -> None:
        self._session_factory = session_factory or get_session_factory(settings, url=url)
        self.session: Session | None = None

    def __enter__(self) -> UnitOfWork:
        self.session = self._session_factory()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self.session is not None
        if exc_type is not None:
            self.session.rollback()
        else:
            self.session.commit()
        self.session.close()
        self.session = None

    def commit(self) -> None:
        assert self.session is not None
        self.session.commit()

    def rollback(self) -> None:
        assert self.session is not None
        self.session.rollback()
