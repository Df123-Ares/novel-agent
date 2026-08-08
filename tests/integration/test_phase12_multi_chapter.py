"""Phase 1.2 multi-chapter writing against local Ollama."""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_agent.application.workflows import book_flow
from novel_agent.domain.book.schemas import CreateBookRequest
from novel_agent.infrastructure.llm.capabilities import probe_model
from novel_agent.infrastructure.llm.ollama_adapter import OllamaAdapter
from novel_agent.infrastructure.persistence.db import create_all_tables, get_engine, reset_engine
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
def test_write_two_chapters_with_growing_facts(ollama_ready: OllamaAdapter, tmp_path: Path) -> None:
    reset_engine()
    url = f"sqlite:///{(tmp_path / 'multi.db').as_posix()}"
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
            book_flow.generate_outline(session, book.id)
            book_flow.lock_outline(session, book.id)
            book_flow.generate_characters(session, book.id)
            book_flow.lock_characters(session, book.id)
            chapters = book_flow.plan_chapters(session, book.id)
            assert len(chapters) >= 2

            loop = book_flow.write_chapters_loop(
                session, book.id, max_chapters=2, auto_confirm=True
            )
            assert len(loop.written) == 2
            assert loop.written[0].chapter_number == 1
            assert loop.written[1].chapter_number == 2
            assert loop.written[0].confirm.get("status") == "confirmed"
            assert loop.written[1].confirm.get("status") == "confirmed"
            # chapter 2 context should see chapter 1
            assert loop.written[1].generate.context_manifest.get("prev_chapter_number") == 1
            assert loop.confirmed_facts_total >= 0

            listed = book_flow.list_chapters(session, book.id)
            confirmed = [c for c in listed if c.status == "confirmed"]
            assert len(confirmed) >= 2
            assert all(c.has_content for c in confirmed[:2])
    finally:
        reset_engine()
