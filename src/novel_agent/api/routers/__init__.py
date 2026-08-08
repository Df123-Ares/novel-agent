"""Book / outline / character / chapter / task routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from novel_agent.api.dependencies import get_db, get_gateway
from novel_agent.api.schemas import ok
from novel_agent.application.workflows import book_flow
from novel_agent.domain.book.schemas import (
    CreateBookRequest,
    UpdateCharacterRequest,
    UpdateOutlineNodeRequest,
)
from novel_agent.infrastructure.llm.gateway import LLMGateway

router = APIRouter(prefix="/api/v1")


@router.post("/books")
def create_book(body: CreateBookRequest, db: Session = Depends(get_db)):
    book = book_flow.create_book(db, body)
    return ok(book.model_dump(), version=book.version)


@router.get("/books/{book_id}")
def get_book(book_id: str, db: Session = Depends(get_db)):
    book = book_flow.get_book(db, book_id)
    return ok(book.model_dump(), version=book.version)


@router.post("/books/{book_id}/outline/generate")
def generate_outline(
    book_id: str,
    db: Session = Depends(get_db),
    gateway: LLMGateway = Depends(get_gateway),
):
    nodes = book_flow.generate_outline(db, book_id, gateway=gateway)
    book = book_flow.get_book(db, book_id)
    return ok([n.model_dump() for n in nodes], version=book.version)


@router.get("/books/{book_id}/outline")
def list_outline(book_id: str, db: Session = Depends(get_db)):
    nodes = book_flow.list_outline(db, book_id)
    return ok([n.model_dump() for n in nodes])


@router.patch("/books/{book_id}/outline/nodes/{node_id}")
def update_outline_node(
    book_id: str,
    node_id: str,
    body: UpdateOutlineNodeRequest,
    db: Session = Depends(get_db),
):
    node = book_flow.update_outline_node(db, book_id, node_id, body)
    return ok(node.model_dump(), version=node.version)


@router.post("/books/{book_id}/outline/lock")
def lock_outline(book_id: str, db: Session = Depends(get_db)):
    book = book_flow.lock_outline(db, book_id)
    return ok(book.model_dump(), version=book.version)


@router.post("/books/{book_id}/characters/generate")
def generate_characters(
    book_id: str,
    db: Session = Depends(get_db),
    gateway: LLMGateway = Depends(get_gateway),
):
    chars = book_flow.generate_characters(db, book_id, gateway=gateway)
    book = book_flow.get_book(db, book_id)
    return ok([c.model_dump() for c in chars], version=book.version)


@router.get("/books/{book_id}/characters")
def list_characters(book_id: str, db: Session = Depends(get_db)):
    chars = book_flow.list_characters(db, book_id)
    return ok([c.model_dump() for c in chars])


@router.patch("/books/{book_id}/characters/{character_id}")
def update_character(
    book_id: str,
    character_id: str,
    body: UpdateCharacterRequest,
    db: Session = Depends(get_db),
):
    char = book_flow.update_character(db, book_id, character_id, body)
    return ok(char.model_dump(), version=char.version)


@router.post("/books/{book_id}/characters/lock")
def lock_characters(book_id: str, db: Session = Depends(get_db)):
    book = book_flow.lock_characters(db, book_id)
    return ok(book.model_dump(), version=book.version)


@router.post("/books/{book_id}/chapters/plan")
def plan_chapters(book_id: str, db: Session = Depends(get_db)):
    chapters = book_flow.plan_chapters(db, book_id)
    book = book_flow.get_book(db, book_id)
    return ok([c.model_dump() for c in chapters], version=book.version)


@router.get("/books/{book_id}/chapters")
def list_chapters(book_id: str, db: Session = Depends(get_db)):
    chapters = book_flow.list_chapters(db, book_id)
    return ok([c.model_dump() for c in chapters])


@router.get("/books/{book_id}/chapters/next")
def get_next_chapter(book_id: str, db: Session = Depends(get_db)):
    chapter = book_flow.get_next_writable_chapter(db, book_id)
    return ok(chapter.model_dump())


@router.post("/books/{book_id}/chapters/write-next")
def write_next_chapter(
    book_id: str,
    db: Session = Depends(get_db),
    gateway: LLMGateway = Depends(get_gateway),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    result = book_flow.write_next_chapter(
        db, book_id, gateway=gateway, idempotency_key=idempotency_key
    )
    return ok(result.model_dump())


@router.get("/chapters/{chapter_id}")
def get_chapter(chapter_id: str, db: Session = Depends(get_db)):
    detail = book_flow.get_chapter(db, chapter_id)
    return ok(detail.model_dump())


@router.post("/chapters/{chapter_id}/generate")
def generate_chapter(
    chapter_id: str,
    db: Session = Depends(get_db),
    gateway: LLMGateway = Depends(get_gateway),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    result = book_flow.generate_chapter(
        db, chapter_id, gateway=gateway, idempotency_key=idempotency_key
    )
    return ok(result.model_dump())


@router.post("/chapters/{chapter_id}/confirm")
def confirm_chapter(
    chapter_id: str,
    force: bool = False,
    db: Session = Depends(get_db),
):
    result = book_flow.confirm_chapter(db, chapter_id, force=force)
    return ok(result)


@router.post("/books/{book_id}/complete")
def complete_book(book_id: str, db: Session = Depends(get_db)):
    result = book_flow.complete_book(db, book_id)
    return ok(result.model_dump(), version=book_flow.get_book(db, book_id).version)


@router.get("/books/{book_id}/export")
def export_book(
    book_id: str,
    format: str = "txt",
    scope: str = "confirmed",
    db: Session = Depends(get_db),
):
    """Export txt. scope=confirmed (default) exports only confirmed chapters; scope=all requires full book."""
    if format != "txt":
        from novel_agent.domain.errors import PreconditionError

        raise PreconditionError("only txt is supported in this phase", code="EXPORT_FORMAT")
    result = book_flow.export_book_txt(db, book_id, scope=scope)
    return ok(result.model_dump())


@router.get("/tasks/{task_id}")
def get_task(task_id: str, db: Session = Depends(get_db)):
    task = book_flow.get_task(db, task_id)
    return ok(task.model_dump())
