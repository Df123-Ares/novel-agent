"""Phase 1.2: sequential writing and context assembly (no LLM)."""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_agent.application.workflows import book_flow
from novel_agent.domain.book.schemas import CreateBookRequest
from novel_agent.domain.errors import PreconditionError, new_id
from novel_agent.infrastructure.persistence.db import (
    create_all_tables,
    get_engine,
    reset_engine,
)
from novel_agent.infrastructure.persistence.models import (
    ChapterRow,
    ChapterVersionRow,
    CharacterRow,
    ConfirmedFactRow,
    OutlineNodeRow,
)
from novel_agent.infrastructure.persistence.unit_of_work import UnitOfWork
from novel_agent.settings import Settings


@pytest.fixture()
def db_url(tmp_path: Path) -> str:
    reset_engine()
    url = f"sqlite:///{(tmp_path / 'seq.db').as_posix()}"
    create_all_tables(get_engine(url=url))
    yield url
    reset_engine()


def _seed_book_with_two_chapters(session) -> tuple[str, str, str]:
    book = book_flow.create_book(
        session, CreateBookRequest(title="T", premise="梗概", length="short")
    )
    b = book_flow._get_book(session, book.id)
    b.outline_locked = True
    b.characters_locked = True
    session.add(
        CharacterRow(
            id=new_id(),
            book_id=book.id,
            name="林澄",
            personality="冷静",
            appearance="清瘦",
            background="管理员",
            role="protagonist",
            locked=True,
        )
    )
    node1 = OutlineNodeRow(
        id=new_id(),
        book_id=book.id,
        parent_id=None,
        level="chapter_goal",
        title="一",
        summary="开端",
        sort_order=0,
        locked=True,
    )
    node2 = OutlineNodeRow(
        id=new_id(),
        book_id=book.id,
        parent_id=None,
        level="chapter_goal",
        title="二",
        summary="发展",
        sort_order=1,
        locked=True,
    )
    session.add(node1)
    session.add(node2)
    session.flush()
    ch1 = ChapterRow(
        id=new_id(),
        book_id=book.id,
        outline_node_id=node1.id,
        number=1,
        title="一",
        goal="开端",
        target_words=1000,
        status="planned",
    )
    ch2 = ChapterRow(
        id=new_id(),
        book_id=book.id,
        outline_node_id=node2.id,
        number=2,
        title="二",
        goal="发展",
        target_words=1000,
        status="planned",
    )
    session.add(ch1)
    session.add(ch2)
    session.flush()
    return book.id, ch1.id, ch2.id


def test_cannot_generate_ch2_before_ch1_confirmed(db_url: str) -> None:
    with UnitOfWork(url=db_url) as uow:
        session = uow.session
        assert session is not None
        _book_id, _ch1_id, ch2_id = _seed_book_with_two_chapters(session)
        with pytest.raises(PreconditionError) as exc:
            book_flow._ensure_chapter_sequence_ok(session, session.get(ChapterRow, ch2_id))
        assert exc.value.code == "PREV_CHAPTER_NOT_CONFIRMED"


def test_get_next_blocks_when_draft_pending(db_url: str) -> None:
    with UnitOfWork(url=db_url) as uow:
        session = uow.session
        assert session is not None
        book_id, ch1_id, _ch2_id = _seed_book_with_two_chapters(session)
        ch1 = session.get(ChapterRow, ch1_id)
        assert ch1 is not None
        ver = ChapterVersionRow(
            id=new_id(),
            chapter_id=ch1.id,
            title="一",
            content="第一章正文结尾标记ABCDEF",
            summary="开端摘要",
            status="DRAFT",
            word_count=20,
        )
        session.add(ver)
        session.flush()
        ch1.current_version_id = ver.id
        ch1.status = "drafted"
        with pytest.raises(PreconditionError) as exc:
            book_flow.get_next_writable_chapter(session, book_id)
        assert exc.value.code == "CHAPTER_PENDING_CONFIRM"


def test_context_includes_prev_chapter_tail(db_url: str) -> None:
    with UnitOfWork(url=db_url) as uow:
        session = uow.session
        assert session is not None
        book_id, ch1_id, ch2_id = _seed_book_with_two_chapters(session)
        ch1 = session.get(ChapterRow, ch1_id)
        assert ch1 is not None
        ver = ChapterVersionRow(
            id=new_id(),
            chapter_id=ch1.id,
            title="一",
            content="前面的内容" + ("尾" * 20) + "UNIQUE_TAIL_MARKER",
            summary="开端摘要",
            status="CONFIRMED",
            word_count=40,
        )
        session.add(ver)
        session.flush()
        ch1.current_version_id = ver.id
        ch1.status = "confirmed"
        session.add(
            ConfirmedFactRow(
                id=new_id(),
                book_id=book_id,
                chapter_id=ch1.id,
                kind="EVENT",
                subject="林澄",
                claim="发现日志",
                status="CONFIRMED",
            )
        )
        session.flush()
        book = book_flow._get_book(session, book_id)
        ch2 = session.get(ChapterRow, ch2_id)
        assert ch2 is not None
        settings = Settings(prev_chapter_tail_chars=50)
        text, manifest = book_flow._build_chapter_context(
            session, book, ch2, settings=settings
        )
        assert "UNIQUE_TAIL_MARKER" in text
        assert "发现日志" in text
        assert "开端摘要" in text
        assert manifest["prev_chapter_number"] == 1
        assert manifest["prev_chapter_tail_chars"] > 0
        assert manifest["fact_count"] >= 1


def test_get_next_returns_first_planned(db_url: str) -> None:
    with UnitOfWork(url=db_url) as uow:
        session = uow.session
        assert session is not None
        book_id, ch1_id, _ = _seed_book_with_two_chapters(session)
        nxt = book_flow.get_next_writable_chapter(session, book_id)
        assert nxt.id == ch1_id
        assert nxt.has_content is False
