"""Hierarchical arc summary tests for long-novel context compression.

Tests:
1. `_arc_range_for_chapter` correctly maps chapter number to arc index/range
2. `_build_chapter_context` injects arc summaries when available
3. `_build_chapter_context` skips current (incomplete) arc
4. Manifest records injected summary ranges correctly
5. Empty arc table falls back to chapter-only summaries (backward compat)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_agent.application.workflows import book_flow
from novel_agent.domain.book.schemas import CreateBookRequest
from novel_agent.domain.errors import new_id
from novel_agent.infrastructure.persistence.db import (
    create_all_tables,
    get_engine,
    reset_engine,
)
from novel_agent.infrastructure.persistence.models import (
    ArcSummaryRow,
    ChapterRow,
    ChapterVersionRow,
)
from novel_agent.infrastructure.persistence.unit_of_work import UnitOfWork
from novel_agent.settings import Settings


@pytest.fixture()
def db_url(tmp_path: Path) -> str:
    reset_engine()
    url = f"sqlite:///{(tmp_path / 'test_arc.db').as_posix()}"
    engine = get_engine(url=url)
    create_all_tables(engine)
    yield url
    reset_engine()


def _seed_confirmed_chapter(
    session,
    book_id: str,
    number: int,
    title: str,
    summary: str,
    content: str = "章节正文内容。",
) -> ChapterRow:
    """Insert a confirmed chapter with a version (summary + content)."""
    chapter = ChapterRow(
        id=new_id(),
        book_id=book_id,
        number=number,
        title=title,
        goal=f"第{number}章目标",
        target_words=1000,
        status="confirmed",
    )
    session.add(chapter)
    session.flush()
    version = ChapterVersionRow(
        id=new_id(),
        chapter_id=chapter.id,
        title=title,
        content=content,
        summary=summary,
        status="CONFIRMED",
        word_count=len(content),
    )
    session.add(version)
    session.flush()
    chapter.current_version_id = version.id
    session.flush()
    return chapter


def _seed_arc_summary(
    session,
    book_id: str,
    arc_index: int,
    chapter_start: int,
    chapter_end: int,
    summary: str,
    *,
    level: str = "arc",
) -> ArcSummaryRow:
    row = ArcSummaryRow(
        id=new_id(),
        book_id=book_id,
        arc_index=arc_index,
        chapter_start=chapter_start,
        chapter_end=chapter_end,
        level=level,
        summary=summary,
        char_count=len(summary),
    )
    session.add(row)
    session.flush()
    return row


# ── 1. Arc range mapping ──────────────────────────────────────────────────


def test_arc_range_for_chapter_basic() -> None:
    """chapter 1-10 → arc 1, 11-20 → arc 2, etc."""
    assert book_flow._arc_range_for_chapter(1, arc_size=10) == (1, 1, 10)
    assert book_flow._arc_range_for_chapter(10, arc_size=10) == (1, 1, 10)
    assert book_flow._arc_range_for_chapter(11, arc_size=10) == (2, 11, 20)
    assert book_flow._arc_range_for_chapter(25, arc_size=10) == (3, 21, 30)
    assert book_flow._arc_range_for_chapter(50, arc_size=10) == (5, 41, 50)
    assert book_flow._arc_range_for_chapter(51, arc_size=10) == (6, 51, 60)


def test_arc_range_for_chapter_mega() -> None:
    """mega-arc (50 chapters): chapter 1-50 → mega 1, 51-100 → mega 2."""
    assert book_flow._arc_range_for_chapter(1, arc_size=50) == (1, 1, 50)
    assert book_flow._arc_range_for_chapter(50, arc_size=50) == (1, 1, 50)
    assert book_flow._arc_range_for_chapter(51, arc_size=50) == (2, 51, 100)


# ── 2. Context injection ──────────────────────────────────────────────────


def test_build_chapter_context_injects_arc_summaries(db_url: str) -> None:
    """Chapter 25 should see arc 1 (ch 1-10) and arc 2 (ch 11-20) summaries."""
    with UnitOfWork(url=db_url) as uow:
        session = uow.session
        assert session is not None
        book = book_flow.create_book(
            session, CreateBookRequest(title="T", premise="p", length="long")
        )
        book.outline_locked = True
        book.characters_locked = True

        # Seed 24 confirmed chapters (so chapter 25 is the next to write)
        for i in range(1, 25):
            _seed_confirmed_chapter(
                session, book.id, i, f"第{i}章", f"第{i}章发生的事"
            )

        # Seed arc summaries for arc 1 (ch 1-10) and arc 2 (ch 11-20)
        _seed_arc_summary(
            session, book.id, 1, 1, 10, "第一卷：主角登场，发现撕页日志。"
        )
        _seed_arc_summary(
            session, book.id, 2, 11, 20, "第二卷：主角调查纵火案，遇到反派。"
        )
        uow.commit()

        # Chapter 25 is in arc 3 (ch 21-30, not yet summarized)
        chapter_25 = ChapterRow(
            id=new_id(),
            book_id=book.id,
            number=25,
            title="第二十五章",
            goal="主角潜入仓库",
            target_words=1000,
            status="planned",
        )
        session.add(chapter_25)
        uow.commit()

        settings = Settings(arc_size=10, mega_arc_size=50, arc_summaries_in_context=5)
        canon, manifest = book_flow._build_chapter_context(
            session, book, chapter_25, settings=settings
        )

        # Both arc summaries should be injected
        assert manifest["arc_summaries_injected"] == 2
        assert "第一卷：主角登场" in canon
        assert "第二卷：主角调查纵火案" in canon
        # Should record the ranges
        ranges = manifest["arc_summary_ranges"]
        assert {"arc_index": 1, "start": 1, "end": 10} in ranges
        assert {"arc_index": 2, "start": 11, "end": 20} in ranges


def test_build_chapter_context_skips_current_arc(db_url: str) -> None:
    """Chapter 15 (in arc 2) should NOT see arc 2 summary even if it exists."""
    with UnitOfWork(url=db_url) as uow:
        session = uow.session
        assert session is not None
        book = book_flow.create_book(
            session, CreateBookRequest(title="T", premise="p", length="long")
        )
        book.outline_locked = True
        book.characters_locked = True

        # Seed 14 confirmed chapters
        for i in range(1, 15):
            _seed_confirmed_chapter(session, book.id, i, f"第{i}章", f"第{i}章事")

        # Seed arc 1 (ch 1-10) and arc 2 (ch 11-20) — but arc 2 is incomplete
        _seed_arc_summary(session, book.id, 1, 1, 10, "第一卷摘要")
        _seed_arc_summary(session, book.id, 2, 11, 20, "第二卷摘要（不应被注入）")
        uow.commit()

        chapter_15 = ChapterRow(
            id=new_id(),
            book_id=book.id,
            number=15,
            title="第十五章",
            goal="剧情推进",
            target_words=1000,
            status="planned",
        )
        session.add(chapter_15)
        uow.commit()

        settings = Settings(arc_size=10, arc_summaries_in_context=5)
        canon, manifest = book_flow._build_chapter_context(
            session, book, chapter_15, settings=settings
        )

        # Only arc 1 (ch 1-10) should be injected; arc 2 is current arc (skipped)
        assert manifest["arc_summaries_injected"] == 1
        assert "第一卷摘要" in canon
        assert "第二卷摘要（不应被注入）" not in canon


def test_build_chapter_context_no_arc_summaries_falls_back(db_url: str) -> None:
    """When no arc summaries exist (early chapters), context still works."""
    with UnitOfWork(url=db_url) as uow:
        session = uow.session
        assert session is not None
        book = book_flow.create_book(
            session, CreateBookRequest(title="T", premise="p", length="short")
        )
        book.outline_locked = True
        book.characters_locked = True

        # Seed 3 confirmed chapters (arc 1 not yet complete)
        for i in range(1, 4):
            _seed_confirmed_chapter(session, book.id, i, f"第{i}章", f"第{i}章事")
        uow.commit()

        chapter_4 = ChapterRow(
            id=new_id(),
            book_id=book.id,
            number=4,
            title="第四章",
            goal="剧情推进",
            target_words=1000,
            status="planned",
        )
        session.add(chapter_4)
        uow.commit()

        settings = Settings()
        canon, manifest = book_flow._build_chapter_context(
            session, book, chapter_4, settings=settings
        )

        # No arc summaries yet (arc 1 covers ch 1-10, not yet complete)
        assert manifest["arc_summaries_injected"] == 0
        assert manifest["mega_summaries_injected"] == 0
        # But chapter summaries (最近3章) should still work
        assert manifest["summary_chapter_numbers"] == [1, 2, 3]


def test_build_chapter_context_mega_summary_injection(db_url: str) -> None:
    """Chapter 55 should see mega-arc 1 (ch 1-50) summary."""
    with UnitOfWork(url=db_url) as uow:
        session = uow.session
        assert session is not None
        book = book_flow.create_book(
            session, CreateBookRequest(title="T", premise="p", length="long")
        )
        book.outline_locked = True
        book.characters_locked = True

        # Seed a few confirmed chapters to make context non-empty
        for i in range(50, 55):
            _seed_confirmed_chapter(session, book.id, i, f"第{i}章", f"第{i}章事")

        # Seed mega summary for ch 1-50
        _seed_arc_summary(
            session,
            book.id,
            1,
            1,
            50,
            "第一大卷：主角从普通人成长为武林高手，破解多起谜案。",
            level="mega",
        )
        # Seed arc summaries for arc 6 (ch 51-60) — but arc 6 is current, skipped
        _seed_arc_summary(
            session, book.id, 6, 51, 60, "第六卷摘要（current arc, skipped）"
        )
        uow.commit()

        chapter_55 = ChapterRow(
            id=new_id(),
            book_id=book.id,
            number=55,
            title="第五十五章",
            goal="决战时刻",
            target_words=1000,
            status="planned",
        )
        session.add(chapter_55)
        uow.commit()

        settings = Settings(
            arc_size=10,
            mega_arc_size=50,
            arc_summaries_in_context=5,
            mega_arc_summaries_in_context=3,
        )
        canon, manifest = book_flow._build_chapter_context(
            session, book, chapter_55, settings=settings
        )

        # Mega summary should be injected (chapter 55 is in mega 2, so mega 1 visible)
        assert manifest["mega_summaries_injected"] == 1
        assert "主角从普通人成长" in canon
        # Arc 6 (current) should NOT be injected
        assert "第六卷摘要（current arc, skipped）" not in canon


def test_arc_summaries_can_be_disabled_via_settings(db_url: str) -> None:
    """Setting arc_summaries_in_context=0 disables injection entirely."""
    with UnitOfWork(url=db_url) as uow:
        session = uow.session
        assert session is not None
        book = book_flow.create_book(
            session, CreateBookRequest(title="T", premise="p", length="long")
        )
        book.outline_locked = True
        book.characters_locked = True

        for i in range(1, 15):
            _seed_confirmed_chapter(session, book.id, i, f"第{i}章", f"第{i}章事")

        _seed_arc_summary(session, book.id, 1, 1, 10, "第一卷摘要（不应被注入）")
        uow.commit()

        chapter_15 = ChapterRow(
            id=new_id(),
            book_id=book.id,
            number=15,
            title="第十五章",
            goal="剧情推进",
            target_words=1000,
            status="planned",
        )
        session.add(chapter_15)
        uow.commit()

        # Disable arc summary injection
        settings = Settings(arc_summaries_in_context=0, mega_arc_summaries_in_context=0)
        canon, manifest = book_flow._build_chapter_context(
            session, book, chapter_15, settings=settings
        )

        assert manifest["arc_summaries_injected"] == 0
        assert manifest["mega_summaries_injected"] == 0
        assert "第一卷摘要（不应被注入）" not in canon
