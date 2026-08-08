"""Tests for draft quality assessment and confirm / generate quality gate."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from novel_agent.application.workflows import book_flow
from novel_agent.domain.book.schemas import CreateBookRequest
from novel_agent.domain.errors import PreconditionError, new_id
from novel_agent.domain.story.llm_outputs import (
    ChapterCheckLLMOutput,
    ExtractorLLMOutput,
    WriterLLMOutput,
)
from novel_agent.domain.text_quality import assess_draft_quality
from novel_agent.infrastructure.persistence.db import create_all_tables, get_engine, reset_engine
from novel_agent.infrastructure.persistence.models import (
    ChapterRow,
    ChapterVersionRow,
    CharacterRow,
    OutlineNodeRow,
)
from novel_agent.infrastructure.persistence.unit_of_work import UnitOfWork
from novel_agent.settings import Settings


@pytest.fixture()
def db_url(tmp_path: Path) -> str:
    reset_engine()
    url = f"sqlite:///{(tmp_path / 'quality.db').as_posix()}"
    create_all_tables(get_engine(url=url))
    yield url
    reset_engine()


def _seed_writable_chapter(session, *, target_words: int = 1000) -> tuple[str, str]:
    book = book_flow.create_book(
        session, CreateBookRequest(title="T", premise="港口档案馆夜班", length="short")
    )
    b = book_flow._get_book(session, book.id)
    b.outline_locked = True
    b.characters_locked = True
    session.add(
        CharacterRow(
            id=new_id(),
            book_id=book.id,
            name="林默",
            personality="冷静",
            appearance="清瘦",
            background="港口档案馆夜班管理员",
            role="protagonist",
            locked=True,
        )
    )
    node = OutlineNodeRow(
        id=new_id(),
        book_id=book.id,
        parent_id=None,
        level="chapter_goal",
        title="夜班发现",
        summary="在档案馆发现灯塔日志",
        sort_order=0,
        locked=True,
    )
    session.add(node)
    session.flush()
    chapter = ChapterRow(
        id=new_id(),
        book_id=book.id,
        outline_node_id=node.id,
        number=1,
        title="夜班发现",
        goal="在档案馆发现灯塔日志",
        target_words=target_words,
        status="planned",
    )
    session.add(chapter)
    session.flush()
    return book.id, chapter.id


def test_assess_too_short() -> None:
    q = assess_draft_quality("很短的一段话。", min_words=400)
    assert q.too_short is True
    assert q.quality_ok is False
    assert "too_short" in q.reason


def test_assess_ok_long_enough() -> None:
    parts = [
        f"林默在第{i}排档案柜前停下，翻开标号为{i}的卷宗，记下与灯塔有关的一行字。"
        for i in range(1, 30)
    ]
    body = "林默走进港口档案馆。" + "".join(parts)
    q = assess_draft_quality(body, min_words=200)
    assert q.too_short is False
    assert q.repetition_severe is False
    assert q.quality_ok is True


def test_assess_severe_repetition() -> None:
    block = "他的思绪被一阵低语打断，那声音仿佛从灯塔的深处传来。他缓缓站起身，环顾四周。"
    text = "林默走进灯塔。" + block * 8
    q = assess_draft_quality(text, min_words=50, max_trim_ratio=0.25)
    assert q.repetition_truncated is True
    assert q.repetition_severe is True
    assert q.quality_ok is False
    assert q.final_len < q.original_len


def test_confirm_rejects_short_draft(db_url: str) -> None:
    with UnitOfWork(url=db_url) as uow:
        session = uow.session
        assert session is not None
        _book_id, chapter_id = _seed_writable_chapter(session, target_words=1000)
        chapter = session.get(ChapterRow, chapter_id)
        assert chapter is not None
        ver = ChapterVersionRow(
            id=new_id(),
            chapter_id=chapter.id,
            title=chapter.title,
            content="只有一点点正文。",
            summary="短",
            status="DRAFT",
            word_count=8,
        )
        session.add(ver)
        session.flush()
        chapter.current_version_id = ver.id
        chapter.status = "drafted"

        settings = Settings(writer_enforce_quality_on_confirm=True)
        with pytest.raises(PreconditionError) as exc:
            book_flow.confirm_chapter(session, chapter.id, settings=settings)
        assert exc.value.code == "CHAPTER_QUALITY_FAILED"

        ok = book_flow.confirm_chapter(session, chapter.id, force=True, settings=settings)
        assert ok["status"] == "confirmed"


def _unique_long_chapter(min_chars: int = 600) -> str:
    chunks: list[str] = []
    i = 0
    while sum(len(c) for c in chunks) < min_chars:
        i += 1
        chunks.append(
            f"林默在夜班第{i}小时走到港口档案馆第{i}区，"
            f"抽出一本与灯塔相关的日志，看见妹妹名字旁标注着日期{i}。"
        )
    return "".join(chunks)


def test_generate_expands_short_then_marks_quality(db_url: str) -> None:
    short = WriterLLMOutput(
        title="夜班发现",
        content="林默站在档案馆门口。",
        scene_summary="到了门口。",
    )
    long = WriterLLMOutput(
        title="夜班发现",
        content=_unique_long_chapter(700),
        scene_summary="发现灯塔日志线索。",
    )
    extractor = ExtractorLLMOutput(changes=[])

    gateway = MagicMock()
    validator = ChapterCheckLLMOutput(issues=[], overall_assessment="通过")
    gateway.chat_structured.side_effect = [
        (short, {}),
        (long, {}),
        (extractor, {}),
        (validator, {}),
    ]

    with UnitOfWork(url=db_url) as uow:
        session = uow.session
        assert session is not None
        _book_id, chapter_id = _seed_writable_chapter(session, target_words=800)
        settings = Settings(
            writer_max_repair=2,
            writer_min_words_ratio=0.55,
            writer_repair_repetition=True,
            writer_max_trim_ratio=0.25,
        )
        result = book_flow.generate_chapter(
            session, chapter_id, gateway=gateway, settings=settings
        )
        assert result.context_manifest.get("quality_ok") is True
        assert result.context_manifest.get("expanded_count", 0) >= 1
        assert result.version.word_count >= result.context_manifest["min_words"]
        # initial + expand + extractor + validator
        assert gateway.chat_structured.call_count == 4


def test_write_loop_skips_confirm_when_quality_fails(db_url: str) -> None:
    always_short = WriterLLMOutput(
        title="夜班发现",
        content="太短了。",
        scene_summary="无。",
    )
    extractor = ExtractorLLMOutput(changes=[])
    validator = ChapterCheckLLMOutput(issues=[], overall_assessment="通过")
    gateway = MagicMock()
    # initial + 2 repairs + extractor + validator
    gateway.chat_structured.side_effect = [
        (always_short, {}),
        (always_short, {}),
        (always_short, {}),
        (extractor, {}),
        (validator, {}),
    ]

    with UnitOfWork(url=db_url) as uow:
        session = uow.session
        assert session is not None
        book_id, _chapter_id = _seed_writable_chapter(session, target_words=800)
        settings = Settings(writer_max_repair=2, writer_enforce_quality_on_confirm=True)
        loop = book_flow.write_chapters_loop(
            session,
            book_id,
            max_chapters=1,
            auto_confirm=True,
            gateway=gateway,
            settings=settings,
        )
        assert len(loop.written) == 1
        assert loop.written[0].confirm.get("status") == "skipped_quality"
        assert loop.written[0].generate.context_manifest.get("quality_ok") is False
        chapter = session.get(ChapterRow, loop.written[0].chapter_id)
        assert chapter is not None
        assert chapter.status == "drafted"
