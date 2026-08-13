"""RAG fact retrieval tests for `_build_chapter_context`.

Verifies that:
1. `search_relevant_facts` returns fact_ids by bm25 relevance (OR query)
2. `_build_chapter_context` prioritizes RAG hits over chronological order
3. Fallback fills remaining slots with recent facts when RAG underfills
4. manifest correctly records rag_fact_count / fallback_fact_count
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from novel_agent.application.workflows import book_flow
from novel_agent.domain.book.schemas import CreateBookRequest
from novel_agent.domain.errors import new_id
from novel_agent.infrastructure.persistence.db import (
    create_all_tables,
    get_engine,
    reset_engine,
)
from novel_agent.infrastructure.persistence.fts import (
    search_relevant_facts,
    upsert_fts,
)
from novel_agent.infrastructure.persistence.models import (
    ChapterRow,
    ConfirmedFactRow,
)
from novel_agent.infrastructure.persistence.unit_of_work import UnitOfWork
from novel_agent.settings import Settings


@pytest.fixture()
def db_url(tmp_path: Path) -> str:
    reset_engine()
    url = f"sqlite:///{(tmp_path / 'test_rag.db').as_posix()}"
    engine = get_engine(url=url)
    create_all_tables(engine)
    yield url
    reset_engine()


def _seed_book_with_facts(session, book_id: str, facts: list[dict]) -> None:
    """Insert confirmed facts and mirror them into FTS (as confirm_chapter does)."""
    for f in facts:
        fact_id = f.get("id") or new_id()
        row = ConfirmedFactRow(
            id=fact_id,
            book_id=book_id,
            chapter_id=f["chapter_id"],
            source_change_id=None,
            kind=f.get("kind", "EVENT"),
            subject=f["subject"],
            claim=f["claim"],
            evidence_quote=None,
            status="CONFIRMED",
        )
        session.add(row)
        session.flush()
        upsert_fts(
            session,
            doc_id=f"fact:{fact_id}",
            book_id=book_id,
            doc_type="fact",
            title=f["subject"],
            body=f["claim"],
        )
    session.flush()


def test_search_relevant_facts_returns_or_matches(db_url: str) -> None:
    """OR query should return any fact whose tokens match the query."""
    with UnitOfWork(url=db_url) as uow:
        session = uow.session
        assert session is not None
        book = book_flow.create_book(
            session, CreateBookRequest(title="T", premise="p", length="short")
        )
        chapter = ChapterRow(
            id=new_id(),
            book_id=book.id,
            number=1,
            title="开端",
            goal="林澄调查仓库",
            target_words=1000,
            status="planned",
        )
        session.add(chapter)
        session.flush()

        _seed_book_with_facts(
            session,
            book.id,
            [
                {
                    "chapter_id": chapter.id,
                    "subject": "林澄",
                    "claim": "林澄在仓库发现撕页日志",
                },
                {
                    "chapter_id": chapter.id,
                    "subject": "纵火案",
                    "claim": "十年前发生纵火案",
                },
                {
                    "chapter_id": chapter.id,
                    "subject": "天气",
                    "claim": "那天下午下大雨",
                },
            ],
        )
        uow.commit()

        # Query matching "林澄" + "仓库" should return the first fact
        ids = search_relevant_facts(session, book.id, "林澄 仓库", limit=10)
        assert len(ids) >= 1
        rows = session.scalars(select(ConfirmedFactRow)).all()
        first_match = next(r for r in rows if r.id == ids[0])
        assert "林澄" in first_match.claim or "仓库" in first_match.claim


def test_search_relevant_facts_empty_query_returns_empty(db_url: str) -> None:
    """Empty / whitespace-only queries must not crash and return []."""
    with UnitOfWork(url=db_url) as uow:
        session = uow.session
        assert session is not None
        book = book_flow.create_book(
            session, CreateBookRequest(title="T", premise="p", length="short")
        )
        uow.commit()
        assert search_relevant_facts(session, book.id, "", limit=10) == []
        assert search_relevant_facts(session, book.id, "   ", limit=10) == []


def test_build_chapter_context_rag_prioritizes_relevant_facts(db_url: str) -> None:
    """Chapter goal matching fact A should put A ahead of chronologically-newer fact B."""
    with UnitOfWork(url=db_url) as uow:
        session = uow.session
        assert session is not None
        book = book_flow.create_book(
            session, CreateBookRequest(title="T", premise="p", length="short")
        )
        book.outline_locked = True
        book.characters_locked = True

        chapter = ChapterRow(
            id=new_id(),
            book_id=book.id,
            number=1,
            title="仓库探秘",
            goal="林澄深夜潜入仓库寻找纵火证据",
            target_words=1000,
            status="planned",
        )
        session.add(chapter)
        session.flush()

        _seed_book_with_facts(
            session,
            book.id,
            [
                # Relevant: matches "仓库" + "林澄" + "纵火"
                {
                    "chapter_id": chapter.id,
                    "subject": "林澄",
                    "claim": "林澄在旧仓库发现撕页日志，记录纵火细节",
                },
                # Relevant: matches "纵火"
                {
                    "chapter_id": chapter.id,
                    "subject": "纵火案",
                    "claim": "十年前的纵火案至今未破",
                },
                # Irrelevant: no keyword overlap with chapter goal
                {
                    "chapter_id": chapter.id,
                    "subject": "天气",
                    "claim": "那天下大雨，街上没人",
                },
                {
                    "chapter_id": chapter.id,
                    "subject": "早饭",
                    "claim": "林澄早上吃了豆浆油条",
                },
            ],
        )
        uow.commit()

        settings = Settings(max_facts_in_context=4)
        canon, manifest = book_flow._build_chapter_context(
            session, book, chapter, settings=settings
        )

        # At least one of the two relevant facts (林澄/仓库/纵火) should be in the RAG hits
        assert manifest["rag_fact_count"] >= 1
        assert manifest["fact_count"] == 4  # 4 facts total, max=4
        # The "天气" fact (irrelevant) should not appear before the relevant ones
        assert "天气" not in canon.split("林澄在旧仓库")[0] if "林澄在旧仓库" in canon else True


def test_build_chapter_context_fallback_fills_when_rag_underfills(db_url: str) -> None:
    """When RAG returns fewer than max_facts, recent facts fill the remaining slots."""
    with UnitOfWork(url=db_url) as uow:
        session = uow.session
        assert session is not None
        book = book_flow.create_book(
            session, CreateBookRequest(title="T", premise="p", length="short")
        )
        book.outline_locked = True
        book.characters_locked = True

        chapter = ChapterRow(
            id=new_id(),
            book_id=book.id,
            number=1,
            title="无关章节",
            goal="主角吃早饭，完全没有匹配关键词",
            target_words=1000,
            status="planned",
        )
        session.add(chapter)
        session.flush()

        # 3 facts, none match the chapter goal keywords
        _seed_book_with_facts(
            session,
            book.id,
            [
                {"chapter_id": chapter.id, "subject": "火灾", "claim": "仓库失火"},
                {"chapter_id": chapter.id, "subject": "日记", "claim": "撕页日志"},
                {"chapter_id": chapter.id, "subject": "警察", "claim": "警方介入"},
            ],
        )
        uow.commit()

        settings = Settings(max_facts_in_context=5)
        canon, manifest = book_flow._build_chapter_context(
            session, book, chapter, settings=settings
        )

        # RAG may return 0 hits (no keyword overlap) → fallback fills all 3 slots
        assert manifest["fact_count"] == 3
        assert manifest["rag_fact_count"] + manifest["fallback_fact_count"] == 3
        # All 3 facts appear (via fallback)
        assert "仓库失火" in canon
        assert "撕页日志" in canon
        assert "警方介入" in canon


def test_build_chapter_context_manifest_records_rag_query(db_url: str) -> None:
    """manifest should expose rag_query for debugging."""
    with UnitOfWork(url=db_url) as uow:
        session = uow.session
        assert session is not None
        book = book_flow.create_book(
            session, CreateBookRequest(title="T", premise="p", length="short")
        )
        book.outline_locked = True
        book.characters_locked = True

        chapter = ChapterRow(
            id=new_id(),
            book_id=book.id,
            number=1,
            title="测试章节",
            goal="测试目标",
            target_words=1000,
            status="planned",
        )
        session.add(chapter)
        uow.commit()

        settings = Settings()
        _, manifest = book_flow._build_chapter_context(
            session, book, chapter, settings=settings
        )
        assert manifest["rag_query"] == "测试章节 测试目标"
