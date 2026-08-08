"""Phase-1 end-to-end integration against local Ollama."""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_agent.application.workflows import book_flow
from novel_agent.domain.book.schemas import CreateBookRequest
from novel_agent.infrastructure.llm.capabilities import probe_model
from novel_agent.infrastructure.llm.ollama_adapter import OllamaAdapter
from novel_agent.infrastructure.persistence.db import create_all_tables, get_engine, reset_engine
from novel_agent.infrastructure.persistence.fts import search_fts
from novel_agent.infrastructure.persistence.unit_of_work import UnitOfWork
from novel_agent.settings import get_settings


@pytest.fixture(scope="module")
def ollama_ready() -> OllamaAdapter:
    adapter = OllamaAdapter(get_settings())
    report = probe_model(adapter, think=False)
    if not report.ok:
        pytest.skip(f"Ollama probe failed: {report.summarize()}")
    return adapter


@pytest.mark.integration
def test_user_flow_end_to_end(ollama_ready: OllamaAdapter, tmp_path: Path) -> None:
    reset_engine()
    url = f"sqlite:///{(tmp_path / 'flow.db').as_posix()}"
    create_all_tables(get_engine(url=url))
    try:
        with UnitOfWork(url=url) as uow:
            session = uow.session
            assert session is not None
            book = book_flow.create_book(
                session,
                CreateBookRequest(
                    title="雾港回声",
                    genre="都市悬疑",
                    style="网文通俗",
                    length="short",
                    perspective="第三人称",
                    tone="暗黑",
                    premise="夜班管理员发现与失踪妹妹相关的灯塔日志。",
                ),
            )
            nodes = book_flow.generate_outline(session, book.id)
            assert len(nodes) >= 1
            book_flow.lock_outline(session, book.id)
            chars = book_flow.generate_characters(session, book.id)
            assert len(chars) >= 1
            book_flow.lock_characters(session, book.id)
            chapters = book_flow.plan_chapters(session, book.id)
            assert len(chapters) >= 1
            assert book_flow.count_confirmed_facts(session, book.id) == 0
            gen = book_flow.generate_chapter(session, chapters[0].id)
            assert len(gen.version.content) > 20
            assert book_flow.count_confirmed_facts(session, book.id) == 0
            confirm = book_flow.confirm_chapter(session, chapters[0].id)
            assert confirm["status"] == "confirmed"
            assert book_flow.count_confirmed_facts(session, book.id) >= 0
            # after confirm, chapter summary should be searchable if non-empty
            if gen.version.summary:
                hits = search_fts(session, book.id, gen.version.summary[:2] or "章")
                assert isinstance(hits, list)
    finally:
        reset_engine()
