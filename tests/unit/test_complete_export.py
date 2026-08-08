"""Word-count helpers and complete/export (no LLM)."""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_agent.application.workflows import book_flow
from novel_agent.domain.book.schemas import CreateBookRequest
from novel_agent.domain.errors import PreconditionError, new_id
from novel_agent.infrastructure.persistence.db import create_all_tables, get_engine, reset_engine
from novel_agent.infrastructure.persistence.models import (
    ChapterRow,
    ChapterVersionRow,
    CharacterRow,
)
from novel_agent.infrastructure.persistence.unit_of_work import UnitOfWork
from novel_agent.settings import Settings


@pytest.fixture()
def db_url(tmp_path: Path) -> str:
    reset_engine()
    url = f"sqlite:///{(tmp_path / 'finish.db').as_posix()}"
    create_all_tables(get_engine(url=url))
    yield url
    reset_engine()


def test_writer_num_predict_scales_with_target() -> None:
    settings = Settings(writer_num_predict_floor=4096, writer_num_predict_ceil=12288)
    assert book_flow._writer_num_predict(800, settings) == 4096
    big = book_flow._writer_num_predict(3333, settings)
    assert big >= 4096
    assert big == min(12288, int(3333 * 1.6) + 1024)


def test_complete_requires_all_confirmed(db_url: str) -> None:
    with UnitOfWork(url=db_url) as uow:
        session = uow.session
        assert session is not None
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
                name="A",
                personality="p",
                appearance="a",
                background="b",
                role="protagonist",
                locked=True,
            )
        )
        ch = ChapterRow(
            id=new_id(),
            book_id=book.id,
            number=1,
            title="一",
            goal="g",
            target_words=1000,
            status="planned",
        )
        session.add(ch)
        session.flush()
        with pytest.raises(PreconditionError) as exc:
            book_flow.complete_book(session, book.id)
        assert exc.value.code == "BOOK_INCOMPLETE"


def test_complete_and_export_txt(db_url: str, tmp_path: Path) -> None:
    with UnitOfWork(url=db_url) as uow:
        session = uow.session
        assert session is not None
        book = book_flow.create_book(
            session, CreateBookRequest(title="雾港", premise="梗概", length="short")
        )
        b = book_flow._get_book(session, book.id)
        b.outline_locked = True
        b.characters_locked = True
        ch1 = ChapterRow(
            id=new_id(),
            book_id=book.id,
            number=1,
            title="开端",
            goal="g1",
            target_words=1000,
            status="planned",
        )
        ch2 = ChapterRow(
            id=new_id(),
            book_id=book.id,
            number=2,
            title="发展",
            goal="g2",
            target_words=1000,
            status="planned",
        )
        session.add(ch1)
        session.add(ch2)
        session.flush()
        for ch, body in ((ch1, "第一章正文内容甲。"), (ch2, "第二章正文内容乙。")):
            ver = ChapterVersionRow(
                id=new_id(),
                chapter_id=ch.id,
                title=ch.title,
                content=body,
                summary=f"{ch.title}摘要",
                status="CONFIRMED",
                word_count=len(body),
            )
            session.add(ver)
            session.flush()
            ch.current_version_id = ver.id
            ch.status = "confirmed"
        session.flush()

        done = book_flow.complete_book(session, book.id)
        assert done.status == "completed"
        assert done.chapter_count == 2
        assert done.total_chars == len("第一章正文内容甲。") + len("第二章正文内容乙。")

        settings = Settings(export_dir=tmp_path / "exports")
        exported = book_flow.export_book_txt(session, book.id, settings=settings, scope="all")
        path = Path(exported.path)
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        assert "第一章正文内容甲" in text
        assert "第二章正文内容乙" in text
        assert "雾港" in text
        assert exported.scope == "all"
        assert exported.chapter_count == 2


def test_export_confirmed_partial(db_url: str, tmp_path: Path) -> None:
    with UnitOfWork(url=db_url) as uow:
        session = uow.session
        assert session is not None
        book = book_flow.create_book(
            session, CreateBookRequest(title="雾港", premise="梗概", length="short")
        )
        b = book_flow._get_book(session, book.id)
        b.outline_locked = True
        b.characters_locked = True
        ch1 = ChapterRow(
            id=new_id(),
            book_id=book.id,
            number=1,
            title="开端",
            goal="g1",
            target_words=1000,
            status="planned",
        )
        ch2 = ChapterRow(
            id=new_id(),
            book_id=book.id,
            number=2,
            title="未写",
            goal="g2",
            target_words=1000,
            status="planned",
        )
        session.add(ch1)
        session.add(ch2)
        session.flush()
        ver = ChapterVersionRow(
            id=new_id(),
            chapter_id=ch1.id,
            title="开端",
            content="仅第一章正文。",
            summary="摘要一",
            status="CONFIRMED",
            word_count=7,
        )
        session.add(ver)
        session.flush()
        ch1.current_version_id = ver.id
        ch1.status = "confirmed"
        session.flush()

        with pytest.raises(PreconditionError) as exc:
            book_flow.export_book_txt(session, book.id, scope="all")
        assert exc.value.code == "BOOK_INCOMPLETE"

        settings = Settings(export_dir=tmp_path / "exports")
        exported = book_flow.export_book_txt(
            session, book.id, settings=settings, scope="confirmed"
        )
        assert exported.chapter_count == 1
        assert exported.planned_chapter_count == 2
        assert exported.scope == "confirmed"
        text = Path(exported.path).read_text(encoding="utf-8")
        assert "仅第一章正文" in text
        assert "部分导出" in text
        assert "未写" not in text or "第2章" not in text
