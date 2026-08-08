"""Phase-1 persistence and confirm transaction tests (no LLM for confirm path with fixtures)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from novel_agent.application.workflows import book_flow
from novel_agent.domain.book.schemas import CreateBookRequest, UpdateOutlineNodeRequest
from novel_agent.domain.errors import ConflictError, PreconditionError
from novel_agent.infrastructure.persistence.db import create_all_tables, get_engine, reset_engine
from novel_agent.infrastructure.persistence.fts import search_fts
from novel_agent.infrastructure.persistence.models import (
    CandidateChangeRow,
    CandidateChangeSetRow,
    ChapterRow,
    ChapterVersionRow,
    ConfirmedFactRow,
    OutlineNodeRow,
)
from novel_agent.infrastructure.persistence.unit_of_work import UnitOfWork
from novel_agent.domain.errors import new_id


@pytest.fixture()
def db_url(tmp_path: Path) -> str:
    reset_engine()
    url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    engine = get_engine(url=url)
    create_all_tables(engine)
    yield url
    reset_engine()


def test_create_book_and_version_conflict(db_url: str) -> None:
    with UnitOfWork(url=db_url) as uow:
        assert uow.session is not None
        book = book_flow.create_book(
            uow.session,
            CreateBookRequest(
                title="T",
                premise="一句话",
                length="short",
            ),
        )
        node = OutlineNodeRow(
            id=new_id(),
            book_id=book.id,
            parent_id=None,
            level="vision",
            title="愿景",
            summary="s",
            sort_order=0,
        )
        uow.session.add(node)
        uow.session.flush()
        book_flow.update_outline_node(
            uow.session,
            book.id,
            node.id,
            UpdateOutlineNodeRequest(title="新标题", expected_version=1),
        )
        with pytest.raises(ConflictError):
            book_flow.update_outline_node(
                uow.session,
                book.id,
                node.id,
                UpdateOutlineNodeRequest(title="再改", expected_version=1),
            )


def test_confirm_moves_candidates_to_facts(db_url: str) -> None:
    with UnitOfWork(url=db_url) as uow:
        session = uow.session
        assert session is not None
        book = book_flow.create_book(
            session,
            CreateBookRequest(title="T", premise="梗概", length="short"),
        )
        # manually lock path prerequisites
        node = OutlineNodeRow(
            id=new_id(),
            book_id=book.id,
            parent_id=None,
            level="chapter_goal",
            title="开端",
            summary="发现日志",
            sort_order=0,
            locked=True,
        )
        session.add(node)
        session.flush()
        # mark book locks without going through character generate
        b = book_flow._get_book(session, book.id)
        b.outline_locked = True
        b.characters_locked = True
        from novel_agent.infrastructure.persistence.models import CharacterRow

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
        chapter = ChapterRow(
            id=new_id(),
            book_id=book.id,
            outline_node_id=node.id,
            number=1,
            title="开端",
            goal="发现日志",
            target_words=1000,
            status="planned",
        )
        session.add(chapter)
        session.flush()
        version = ChapterVersionRow(
            id=new_id(),
            chapter_id=chapter.id,
            title="开端",
            content="林澄发现了撕页日志。",
            summary="发现日志",
            status="DRAFT",
            word_count=10,
        )
        session.add(version)
        session.flush()
        chapter.current_version_id = version.id
        chapter.status = "drafted"
        cs = CandidateChangeSetRow(
            id=new_id(),
            book_id=book.id,
            chapter_id=chapter.id,
            chapter_version_id=version.id,
            status="PROPOSED",
        )
        session.add(cs)
        session.flush()
        session.add(
            CandidateChangeRow(
                id=new_id(),
                change_set_id=cs.id,
                kind="EVENT",
                subject="林澄",
                claim="发现撕页日志",
                evidence_quote="发现了撕页日志",
                confidence=0.9,
                status="PROPOSED",
            )
        )
        session.flush()

        assert book_flow.count_confirmed_facts(session, book.id) == 0
        result = book_flow.confirm_chapter(session, chapter.id, force=True)
        assert result["facts_added"] == 1
        assert book_flow.count_confirmed_facts(session, book.id) == 1
        facts = session.scalars(select(ConfirmedFactRow)).all()
        assert facts[0].claim == "发现撕页日志"
        hits = search_fts(session, book.id, "日志")
        assert len(hits) >= 1


def test_get_chapter_returns_content(db_url: str) -> None:
    with UnitOfWork(url=db_url) as uow:
        session = uow.session
        assert session is not None
        book = book_flow.create_book(
            session, CreateBookRequest(title="T", premise="梗概", length="short")
        )
        chapter = ChapterRow(
            id=new_id(),
            book_id=book.id,
            outline_node_id=None,
            number=1,
            title="开端",
            goal="发现日志",
            target_words=1000,
            status="drafted",
        )
        session.add(chapter)
        session.flush()
        version = ChapterVersionRow(
            id=new_id(),
            chapter_id=chapter.id,
            title="开端",
            content="林澄发现了撕页日志。",
            summary="发现日志",
            status="DRAFT",
            word_count=10,
        )
        session.add(version)
        session.flush()
        chapter.current_version_id = version.id
        detail = book_flow.get_chapter(session, chapter.id)
        assert detail.current_version is not None
        assert detail.current_version.content == "林澄发现了撕页日志。"


def test_lock_outline_requires_nodes(db_url: str) -> None:
    with UnitOfWork(url=db_url) as uow:
        session = uow.session
        assert session is not None
        book = book_flow.create_book(
            session, CreateBookRequest(title="T", premise="梗概", length="short")
        )
        with pytest.raises(PreconditionError):
            book_flow.lock_outline(session, book.id)
