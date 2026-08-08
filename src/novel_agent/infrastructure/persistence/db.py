"""Database engine / session helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from novel_agent.infrastructure.persistence.models import Base
from novel_agent.settings import Settings, get_settings

_engines: dict[str, Engine] = {}
_session_locals: dict[str, sessionmaker[Session]] = {}


def _configure_sqlite(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record):  # noqa: ANN001
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


def get_engine(settings: Settings | None = None, *, url: str | None = None) -> Engine:
    settings = settings or get_settings()
    db_url = url or settings.database_url
    if db_url in _engines:
        return _engines[db_url]
    connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
    engine = create_engine(db_url, future=True, connect_args=connect_args)
    if db_url.startswith("sqlite"):
        _configure_sqlite(engine)
    _engines[db_url] = engine
    return engine


def get_session_factory(settings: Settings | None = None, *, url: str | None = None) -> sessionmaker[Session]:
    settings = settings or get_settings()
    db_url = url or settings.database_url
    engine = get_engine(settings, url=url)
    if db_url not in _session_locals:
        _session_locals[db_url] = sessionmaker(
            bind=engine, autoflush=False, autocommit=False, future=True
        )
    return _session_locals[db_url]


def reset_engine() -> None:
    for engine in _engines.values():
        engine.dispose()
    _engines.clear()
    _session_locals.clear()


def create_all_tables(engine: Engine | None = None) -> None:
    eng = engine or get_engine()
    Base.metadata.create_all(eng)
    with eng.begin() as conn:
        conn.execute(
            text(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS fts_documents USING fts5(
                    doc_id UNINDEXED,
                    book_id UNINDEXED,
                    doc_type UNINDEXED,
                    title,
                    body,
                    tokenize = 'unicode61'
                );
                """
            )
        )


@contextmanager
def session_scope(settings: Settings | None = None, *, url: str | None = None) -> Iterator[Session]:
    factory = get_session_factory(settings, url=url)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
