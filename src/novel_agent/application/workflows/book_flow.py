"""Book / outline / character / chapter workflows for phase-1 MVP."""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from novel_agent.domain.book.llm_outputs import CharactersLLMOutput, OutlineLLMOutput, OutlineNodeLLM
from novel_agent.domain.book.schemas import (
    BookOut,
    CandidateChangeOut,
    ChapterDetailOut,
    ChapterOut,
    ChapterValidationIssue,
    ChapterVersionOut,
    CharacterOut,
    CompleteBookResult,
    CreateBookRequest,
    ExportBookResult,
    GenerateChapterResult,
    OutlineNodeOut,
    TaskOut,
    UpdateCharacterRequest,
    UpdateOutlineNodeRequest,
    WriteLoopItem,
    WriteLoopResult,
)
from novel_agent.domain.errors import ConflictError, NotFoundError, PreconditionError, new_id
from novel_agent.domain.story.llm_outputs import (
    ChapterCheckLLMOutput,
    ExtractorLLMOutput,
    WriterLLMOutput,
)
from novel_agent.domain.tasks import TaskStatus
from novel_agent.domain.text_quality import assess_draft_quality
from novel_agent.infrastructure.llm.gateway import LLMGateway
from novel_agent.infrastructure.persistence.fts import upsert_fts
from novel_agent.infrastructure.persistence.models import (
    BookRow,
    CandidateChangeRow,
    CandidateChangeSetRow,
    ChapterRow,
    ChapterVersionRow,
    CharacterRow,
    ConfirmedFactRow,
    OutlineNodeRow,
    TaskRow,
)
from novel_agent.infrastructure.prompts import PromptRegistry
from novel_agent.settings import Settings, get_settings


_ARC_HEADER_RE = re.compile(r"^第\s*[0-9一二三四五六七八九十百]+\s*[卷部篇]")
_CHAPTER_HEADER_RE = re.compile(r"^第\s*[0-9一二三四五六七八九十百]+\s*[章回节]")


def _book_out(row: BookRow) -> BookOut:
    return BookOut(
        id=row.id,
        title=row.title,
        genre=row.genre,
        style=row.style,
        length=row.length,
        perspective=row.perspective,
        tone=row.tone,
        premise=row.premise,
        status=row.status,
        version=row.version,
        outline_locked=row.outline_locked,
        characters_locked=row.characters_locked,
    )


def _outline_out(row: OutlineNodeRow) -> OutlineNodeOut:
    return OutlineNodeOut(
        id=row.id,
        parent_id=row.parent_id,
        level=row.level,
        title=row.title,
        summary=row.summary,
        sort_order=row.sort_order,
        locked=row.locked,
        version=row.version,
    )


def _character_out(row: CharacterRow) -> CharacterOut:
    return CharacterOut(
        id=row.id,
        name=row.name,
        personality=row.personality,
        appearance=row.appearance,
        background=row.background,
        role=row.role,
        locked=row.locked,
        version=row.version,
    )


def _chapter_out(row: ChapterRow) -> ChapterOut:
    return ChapterOut(
        id=row.id,
        number=row.number,
        title=row.title,
        goal=row.goal,
        target_words=row.target_words,
        status=row.status,
        outline_node_id=row.outline_node_id,
        current_version_id=row.current_version_id,
        has_content=bool(row.current_version_id),
    )


def _get_book(session: Session, book_id: str) -> BookRow:
    row = session.get(BookRow, book_id)
    if row is None:
        raise NotFoundError(f"book not found: {book_id}", code="BOOK_NOT_FOUND")
    return row


def _delete_book_chapters(session: Session, book_id: str) -> None:
    """Delete a book's chapters with all dependent rows (facts / change sets / versions / FTS).

    The DB enforces FKs with no ondelete cascades, so dependents must be removed
    explicitly before the chapters themselves, otherwise a delete raises IntegrityError.
    """
    chapter_ids = list(
        session.scalars(select(ChapterRow.id).where(ChapterRow.book_id == book_id)).all()
    )
    if not chapter_ids:
        return
    set_ids = list(
        session.scalars(
            select(CandidateChangeSetRow.id).where(
                CandidateChangeSetRow.chapter_id.in_(chapter_ids)
            )
        ).all()
    )
    if set_ids:
        session.execute(
            delete(CandidateChangeRow).where(CandidateChangeRow.change_set_id.in_(set_ids))
        )
    session.execute(delete(ConfirmedFactRow).where(ConfirmedFactRow.chapter_id.in_(chapter_ids)))
    if set_ids:
        session.execute(
            delete(CandidateChangeSetRow).where(CandidateChangeSetRow.chapter_id.in_(chapter_ids))
        )
    session.execute(
        delete(ChapterVersionRow).where(ChapterVersionRow.chapter_id.in_(chapter_ids))
    )
    session.execute(delete(ChapterRow).where(ChapterRow.id.in_(chapter_ids)))
    session.execute(text("DELETE FROM fts_documents WHERE book_id = :book_id"), {"book_id": book_id})
    session.flush()


def _delete_book_outline(session: Session, book_id: str) -> None:
    """Delete a book's outline nodes in leaf-first order.

    outline_nodes has a self-referential FK without ondelete; deleting a parent while
    its children still exist raises IntegrityError. Deleting children before parents
    keeps the self-FK satisfied on every step.
    """
    present = session.scalars(
        select(OutlineNodeRow).where(OutlineNodeRow.book_id == book_id)
    ).all()
    while present:
        present_ids = {n.id for n in present}
        referenced = {n.parent_id for n in present if n.parent_id}
        deletable = [n for n in present if n.id not in referenced]
        if not deletable:
            deletable = list(present)  # defensive: break cycles, delete in any order
        for n in deletable:
            session.delete(n)
        session.flush()
        present = [n for n in present if n.id not in {d.id for d in deletable}]


def create_book(session: Session, req: CreateBookRequest) -> BookOut:
    row = BookRow(
        id=new_id(),
        title=req.title,
        genre=req.genre,
        style=req.style,
        length=req.length,
        perspective=req.perspective,
        tone=req.tone,
        premise=req.premise,
        status="draft",
        version=1,
    )
    session.add(row)
    session.flush()
    return _book_out(row)


def get_book(session: Session, book_id: str) -> BookOut:
    return _book_out(_get_book(session, book_id))


def _persist_outline_tree(
    session: Session,
    book_id: str,
    node: OutlineNodeLLM,
    parent_id: str | None,
    sort_order: int,
) -> None:
    row = OutlineNodeRow(
        id=new_id(),
        book_id=book_id,
        parent_id=parent_id,
        level=node.level,
        title=node.title,
        summary=node.summary,
        sort_order=sort_order,
        locked=False,
        version=1,
    )
    session.add(row)
    session.flush()
    for i, child in enumerate(node.children):
        _persist_outline_tree(session, book_id, child, row.id, i)


def generate_outline(
    session: Session,
    book_id: str,
    *,
    gateway: LLMGateway | None = None,
    settings: Settings | None = None,
) -> list[OutlineNodeOut]:
    settings = settings or get_settings()
    gateway = gateway or LLMGateway(settings=settings)
    book = _get_book(session, book_id)
    if book.outline_locked:
        raise PreconditionError("outline is locked", code="OUTLINE_LOCKED")

    # clear existing nodes / dependent chapters
    _delete_book_chapters(session, book_id)
    _delete_book_outline(session, book_id)

    # ── dynamic chapter count by length ──
    _length_ranges = {
        "short": {"min_arcs": 1, "max_arcs": 2, "min_ch": 2, "max_ch": 4},
        "medium": {"min_arcs": 2, "max_arcs": 4, "min_ch": 3, "max_ch": 6},
        "long": {"min_arcs": 3, "max_arcs": 6, "min_ch": 4, "max_ch": 8},
    }
    lr = _length_ranges.get(book.length, _length_ranges["medium"])

    # ── include character info if already locked ──
    characters_text = ""
    if book.characters_locked:
        chars = list(
            session.scalars(
                select(CharacterRow).where(CharacterRow.book_id == book_id)
            ).all()
        )
        if chars:
            parts = ["## 已锁定人物（大纲必须围绕这些角色展开）\n"]
            for c in chars:
                parts.append(
                    f"- **{c.name}**（{c.role}）：性格={c.personality}；外貌={c.appearance}；背景={c.background}"
                )
            characters_text = "\n".join(parts)

    registry = PromptRegistry(settings=settings)
    spec, messages = registry.render(
        "planner/outline.yaml",
        title=book.title,
        genre=book.genre,
        style=book.style,
        length=book.length,
        perspective=book.perspective,
        tone=book.tone,
        premise=book.premise,
        min_arcs=lr["min_arcs"],
        max_arcs=lr["max_arcs"],
        min_chapters_per_arc=lr["min_ch"],
        max_chapters_per_arc=lr["max_ch"],
        characters_text=characters_text,
    )
    task = TaskRow(
        id=new_id(),
        book_id=book_id,
        kind="outline.generate",
        status=TaskStatus.RUNNING.value,
    )
    session.add(task)
    session.flush()
    try:
        out, _ = gateway.chat_structured(
            messages,
            OutlineLLMOutput,
            temperature=spec.default_params.get("temperature", 0.5),
            num_predict=spec.default_params.get("num_predict"),
        )
        _persist_outline_tree(session, book_id, out.root, None, 0)
        book.version += 1
        task.status = TaskStatus.SUCCEEDED.value
        task.result_json = json.dumps({"prompt": spec.prompt_id, "version": spec.version})
    except Exception as exc:  # noqa: BLE001
        task.status = TaskStatus.FAILED.value
        task.error = str(exc)
        raise

    nodes = session.scalars(
        select(OutlineNodeRow)
        .where(OutlineNodeRow.book_id == book_id)
        .order_by(OutlineNodeRow.sort_order)
    ).all()
    return [_outline_out(n) for n in nodes]


def list_outline(session: Session, book_id: str) -> list[OutlineNodeOut]:
    _get_book(session, book_id)
    nodes = session.scalars(
        select(OutlineNodeRow)
        .where(OutlineNodeRow.book_id == book_id)
        .order_by(OutlineNodeRow.sort_order)
    ).all()
    return [_outline_out(n) for n in nodes]


def update_outline_node(
    session: Session, book_id: str, node_id: str, req: UpdateOutlineNodeRequest
) -> OutlineNodeOut:
    book = _get_book(session, book_id)
    if book.outline_locked:
        raise PreconditionError("outline is locked", code="OUTLINE_LOCKED")
    node = session.get(OutlineNodeRow, node_id)
    if node is None or node.book_id != book_id:
        raise NotFoundError(f"outline node not found: {node_id}", code="OUTLINE_NODE_NOT_FOUND")
    if node.version != req.expected_version:
        raise ConflictError(
            "outline node version conflict",
            code="BOOK_VERSION_CONFLICT",
            details={"expected": req.expected_version, "actual": node.version},
        )
    if req.title is not None:
        node.title = req.title
    if req.summary is not None:
        node.summary = req.summary
    node.version += 1
    book.version += 1
    session.flush()
    return _outline_out(node)


def lock_outline(session: Session, book_id: str) -> BookOut:
    book = _get_book(session, book_id)
    nodes = session.scalars(select(OutlineNodeRow).where(OutlineNodeRow.book_id == book_id)).all()
    if not nodes:
        raise PreconditionError("outline is empty", code="OUTLINE_EMPTY")
    for n in nodes:
        n.locked = True
    book.outline_locked = True
    book.version += 1
    session.flush()
    return _book_out(book)


def _outline_text(session: Session, book_id: str) -> str:
    nodes = session.scalars(
        select(OutlineNodeRow)
        .where(OutlineNodeRow.book_id == book_id)
        .order_by(OutlineNodeRow.sort_order)
    ).all()
    lines = [f"[{n.level}] {n.title}: {n.summary}" for n in nodes]
    return "\n".join(lines)


def generate_characters(
    session: Session,
    book_id: str,
    *,
    gateway: LLMGateway | None = None,
    settings: Settings | None = None,
) -> list[CharacterOut]:
    settings = settings or get_settings()
    gateway = gateway or LLMGateway(settings=settings)
    book = _get_book(session, book_id)
    if book.characters_locked:
        raise PreconditionError("characters are locked", code="CHARACTERS_LOCKED")

    for c in session.scalars(select(CharacterRow).where(CharacterRow.book_id == book_id)).all():
        session.delete(c)
    session.flush()

    registry = PromptRegistry(settings=settings)
    spec, messages = registry.render(
        "planner/characters.yaml",
        title=book.title,
        genre=book.genre,
        style=book.style,
        tone=book.tone,
        premise=book.premise,
    )
    task = TaskRow(
        id=new_id(),
        book_id=book_id,
        kind="characters.generate",
        status=TaskStatus.RUNNING.value,
    )
    session.add(task)
    session.flush()
    try:
        out, _ = gateway.chat_structured(
            messages,
            CharactersLLMOutput,
            temperature=spec.default_params.get("temperature", 0.5),
            num_predict=spec.default_params.get("num_predict"),
        )
        for ch in out.characters:
            session.add(
                CharacterRow(
                    id=new_id(),
                    book_id=book_id,
                    name=ch.name,
                    personality=ch.personality,
                    appearance=ch.appearance,
                    background=ch.background,
                    role=ch.role,
                    locked=False,
                    version=1,
                )
            )
        book.version += 1
        task.status = TaskStatus.SUCCEEDED.value
        task.result_json = json.dumps({"count": len(out.characters)})
    except Exception as exc:  # noqa: BLE001
        task.status = TaskStatus.FAILED.value
        task.error = str(exc)
        raise

    session.flush()
    rows = session.scalars(select(CharacterRow).where(CharacterRow.book_id == book_id)).all()
    return [_character_out(r) for r in rows]


def list_characters(session: Session, book_id: str) -> list[CharacterOut]:
    _get_book(session, book_id)
    rows = session.scalars(select(CharacterRow).where(CharacterRow.book_id == book_id)).all()
    return [_character_out(r) for r in rows]


def update_character(
    session: Session, book_id: str, character_id: str, req: UpdateCharacterRequest
) -> CharacterOut:
    book = _get_book(session, book_id)
    if book.characters_locked:
        raise PreconditionError("characters are locked", code="CHARACTERS_LOCKED")
    row = session.get(CharacterRow, character_id)
    if row is None or row.book_id != book_id:
        raise NotFoundError(f"character not found: {character_id}", code="CHARACTER_NOT_FOUND")
    if row.version != req.expected_version:
        raise ConflictError(
            "character version conflict",
            code="BOOK_VERSION_CONFLICT",
            details={"expected": req.expected_version, "actual": row.version},
        )
    for field in ("name", "personality", "appearance", "background", "role"):
        val = getattr(req, field)
        if val is not None:
            setattr(row, field, val)
    row.version += 1
    book.version += 1
    session.flush()
    return _character_out(row)


def lock_characters(session: Session, book_id: str) -> BookOut:
    book = _get_book(session, book_id)
    rows = session.scalars(select(CharacterRow).where(CharacterRow.book_id == book_id)).all()
    if not rows:
        raise PreconditionError("no characters to lock", code="CHARACTERS_EMPTY")
    for r in rows:
        r.locked = True
        upsert_fts(
            session,
            doc_id=f"char:{r.id}",
            book_id=book_id,
            doc_type="character",
            title=r.name,
            body=f"{r.personality}\n{r.appearance}\n{r.background}",
        )
    book.characters_locked = True
    book.version += 1
    session.flush()
    return _book_out(book)


def _length_total_words(settings: Settings, length: str) -> int:
    return {
        "short": settings.words_short,
        "medium": settings.words_medium,
        "long": settings.words_long,
    }.get(length, settings.words_medium)


def plan_chapters(session: Session, book_id: str, *, settings: Settings | None = None) -> list[ChapterOut]:
    settings = settings or get_settings()
    book = _get_book(session, book_id)
    if not book.outline_locked:
        raise PreconditionError("outline must be locked first", code="OUTLINE_NOT_LOCKED")
    if not book.characters_locked:
        raise PreconditionError("characters must be locked first", code="CHARACTERS_NOT_LOCKED")

    _delete_book_chapters(session, book_id)

    goals = session.scalars(
        select(OutlineNodeRow)
        .where(OutlineNodeRow.book_id == book_id, OutlineNodeRow.level == "chapter_goal")
        .order_by(OutlineNodeRow.sort_order)
    ).all()
    if not goals:
        # fallback: use leaf nodes (no children)
        all_nodes = session.scalars(
            select(OutlineNodeRow).where(OutlineNodeRow.book_id == book_id)
        ).all()
        child_parents = {n.parent_id for n in all_nodes if n.parent_id}
        goals = [n for n in all_nodes if n.id not in child_parents]
        goals.sort(key=lambda n: n.sort_order)

    if not goals:
        raise PreconditionError("no chapter goals in outline", code="NO_CHAPTER_GOALS")

    total = _length_total_words(settings, book.length)
    per = max(800, total // len(goals))
    chapters: list[ChapterRow] = []
    for i, node in enumerate(goals, start=1):
        row = ChapterRow(
            id=new_id(),
            book_id=book_id,
            outline_node_id=node.id,
            number=i,
            title=node.title,
            goal=node.summary or node.title,
            target_words=per,
            status="planned",
        )
        session.add(row)
        chapters.append(row)
    book.version += 1
    session.flush()
    return [_chapter_out(c) for c in chapters]


def list_chapters(session: Session, book_id: str) -> list[ChapterOut]:
    _get_book(session, book_id)
    rows = session.scalars(
        select(ChapterRow).where(ChapterRow.book_id == book_id).order_by(ChapterRow.number)
    ).all()
    return [_chapter_out(r) for r in rows]


def get_chapter(session: Session, chapter_id: str) -> ChapterDetailOut:
    chapter = session.get(ChapterRow, chapter_id)
    if chapter is None:
        raise NotFoundError(f"chapter not found: {chapter_id}", code="CHAPTER_NOT_FOUND")
    version_out: ChapterVersionOut | None = None
    if chapter.current_version_id:
        version = session.get(ChapterVersionRow, chapter.current_version_id)
        if version is not None:
            version_out = ChapterVersionOut(
                id=version.id,
                chapter_id=version.chapter_id,
                title=version.title,
                content=version.content,
                summary=version.summary,
                status=version.status,
                word_count=version.word_count,
            )
    return ChapterDetailOut(chapter=_chapter_out(chapter), current_version=version_out)


def _build_canon(book: BookRow, characters: list[CharacterRow], facts: list[ConfirmedFactRow]) -> str:
    lines = [
        f"书名：{book.title}",
        f"类型：{book.genre}",
        f"风格：{book.style}",
        f"视角：{book.perspective}",
        f"基调：{book.tone}",
        f"梗概：{book.premise}",
        "人物：",
    ]
    for c in characters:
        lines.append(
            f"- {c.name}（{c.role}）：性格={c.personality}；外貌={c.appearance}；背景={c.background}"
        )
    if facts:
        lines.append("已确认事实：")
        for f in facts:
            lines.append(f"- [{f.kind}] {f.subject}: {f.claim}")
    return "\n".join(lines)


def _build_chapter_context(
    session: Session,
    book: BookRow,
    chapter: ChapterRow,
    *,
    settings: Settings,
) -> tuple[str, dict[str, Any]]:
    """Assemble writing context + manifest for a chapter draft."""
    characters = list(
        session.scalars(select(CharacterRow).where(CharacterRow.book_id == book.id)).all()
    )
    all_facts = list(
        session.scalars(
            select(ConfirmedFactRow)
            .where(ConfirmedFactRow.book_id == book.id)
            .order_by(ConfirmedFactRow.created_at)
        ).all()
    )
    facts = all_facts[-settings.max_facts_in_context :]

    confirmed_chapters = list(
        session.scalars(
            select(ChapterRow)
            .where(ChapterRow.book_id == book.id, ChapterRow.status == "confirmed")
            .order_by(ChapterRow.number.desc())
        ).all()
    )
    recent_for_summary = list(reversed(confirmed_chapters[:3]))
    summary_lines: list[str] = []
    summary_chapter_numbers: list[int] = []
    for ch in recent_for_summary:
        if not ch.current_version_id:
            continue
        ver = session.get(ChapterVersionRow, ch.current_version_id)
        if ver and ver.summary:
            summary_lines.append(f"第{ch.number}章《{ch.title}》摘要：{ver.summary}")
            summary_chapter_numbers.append(ch.number)

    prev_tail = ""
    prev_number: int | None = None
    prev_tail_len = 0
    if chapter.number > 1:
        prev = session.scalars(
            select(ChapterRow).where(
                ChapterRow.book_id == book.id,
                ChapterRow.number == chapter.number - 1,
            )
        ).first()
        if prev and prev.status == "confirmed" and prev.current_version_id:
            prev_ver = session.get(ChapterVersionRow, prev.current_version_id)
            if prev_ver and prev_ver.content:
                tail_n = settings.prev_chapter_tail_chars
                prev_tail = prev_ver.content[-tail_n:]
                prev_number = prev.number
                prev_tail_len = len(prev_tail)

    canon = _build_canon(book, characters, facts)
    parts = [canon]
    if summary_lines:
        parts.append("前文摘要：\n" + "\n".join(summary_lines))
    if prev_tail:
        parts.append(
            f"上一章（第{prev_number}章）正文结尾（截断 {prev_tail_len} 字）：\n{prev_tail}"
        )
    context_text = "\n\n".join(parts)
    manifest: dict[str, Any] = {
        "fact_count": len(facts),
        "fact_total": len(all_facts),
        "summary_chapter_numbers": summary_chapter_numbers,
        "prev_chapter_number": prev_number,
        "prev_chapter_tail_chars": prev_tail_len,
        "context_chars": len(context_text),
    }
    return context_text, manifest


def _ensure_chapter_sequence_ok(session: Session, chapter: ChapterRow) -> None:
    """Require previous chapter confirmed before writing the next."""
    if chapter.number <= 1:
        return
    prev = session.scalars(
        select(ChapterRow).where(
            ChapterRow.book_id == chapter.book_id,
            ChapterRow.number == chapter.number - 1,
        )
    ).first()
    if prev is None:
        raise PreconditionError(
            f"previous chapter {chapter.number - 1} not found",
            code="PREV_CHAPTER_MISSING",
        )
    if prev.status != "confirmed":
        raise PreconditionError(
            f"chapter {prev.number} must be confirmed before writing chapter {chapter.number}",
            code="PREV_CHAPTER_NOT_CONFIRMED",
            details={"prev_chapter_id": prev.id, "prev_status": prev.status},
        )


def get_next_writable_chapter(session: Session, book_id: str) -> ChapterOut:
    """First non-confirmed chapter in order; drafted-but-unconfirmed blocks later chapters."""
    _get_book(session, book_id)
    chapters = list(
        session.scalars(
            select(ChapterRow).where(ChapterRow.book_id == book_id).order_by(ChapterRow.number)
        ).all()
    )
    if not chapters:
        raise PreconditionError("no chapters planned", code="NO_CHAPTERS")

    for ch in chapters:
        if ch.status == "confirmed":
            continue
        if ch.status == "drafted":
            # Next writable is this chapter only after confirm — surface it as blocked
            raise PreconditionError(
                f"chapter {ch.number} has a draft pending confirmation",
                code="CHAPTER_PENDING_CONFIRM",
                details={"chapter_id": ch.id, "chapter_number": ch.number},
            )
        # planned (or other) — check sequence then return
        _ensure_chapter_sequence_ok(session, ch)
        return _chapter_out(ch)

    raise PreconditionError("all chapters are confirmed", code="NO_NEXT_CHAPTER")


def write_next_chapter(
    session: Session,
    book_id: str,
    *,
    gateway: LLMGateway | None = None,
    settings: Settings | None = None,
    idempotency_key: str | None = None,
) -> GenerateChapterResult:
    nxt = get_next_writable_chapter(session, book_id)
    return generate_chapter(
        session,
        nxt.id,
        gateway=gateway,
        settings=settings,
        idempotency_key=idempotency_key,
    )


def write_chapters_loop(
    session: Session,
    book_id: str,
    *,
    max_chapters: int = 3,
    auto_confirm: bool = True,
    gateway: LLMGateway | None = None,
    settings: Settings | None = None,
) -> WriteLoopResult:
    settings = settings or get_settings()
    if max_chapters < 1:
        raise PreconditionError("max_chapters must be >= 1", code="INVALID_MAX_CHAPTERS")
    written: list[WriteLoopItem] = []
    for _ in range(max_chapters):
        try:
            nxt = get_next_writable_chapter(session, book_id)
        except PreconditionError as exc:
            if exc.code in {"NO_NEXT_CHAPTER", "NO_CHAPTERS"}:
                break
            raise
        gen = generate_chapter(session, nxt.id, gateway=gateway, settings=settings)
        confirm_info: dict[str, Any] = {"status": "skipped"}
        if auto_confirm:
            if not gen.context_manifest.get("quality_ok", False):
                confirm_info = {
                    "status": "skipped_quality",
                    "reason": gen.context_manifest.get("quality_reason") or "quality_failed",
                    "word_count": gen.version.word_count,
                    "min_words": gen.context_manifest.get("min_words"),
                }
                written.append(
                    WriteLoopItem(
                        chapter_number=nxt.number,
                        chapter_id=nxt.id,
                        generate=gen,
                        confirm=confirm_info,
                    )
                )
                break
            try:
                confirm_info = confirm_chapter(session, nxt.id, settings=settings)
            except PreconditionError as exc:
                if exc.code == "CHAPTER_QUALITY_FAILED":
                    confirm_info = {
                        "status": "skipped_quality",
                        "reason": str(exc),
                        "details": exc.details,
                    }
                    written.append(
                        WriteLoopItem(
                            chapter_number=nxt.number,
                            chapter_id=nxt.id,
                            generate=gen,
                            confirm=confirm_info,
                        )
                    )
                    break
                raise
        written.append(
            WriteLoopItem(
                chapter_number=nxt.number,
                chapter_id=nxt.id,
                generate=gen,
                confirm=confirm_info,
            )
        )
        if not auto_confirm:
            break
    return WriteLoopResult(
        book_id=book_id,
        written=written,
        confirmed_facts_total=count_confirmed_facts(session, book_id),
    )


def _writer_num_predict(target_words: int, settings: Settings, floor_from_prompt: int | None = None) -> int:
    """Scale generation budget with planned chapter length (Chinese-heavy text)."""
    floor = max(settings.writer_num_predict_floor, floor_from_prompt or 0)
    # ~1.6 tokens per Chinese char + JSON wrapper headroom
    scaled = int(target_words * 1.6) + 1024
    return max(floor, min(settings.writer_num_predict_ceil, scaled))


def _min_words_for_chapter(target_words: int, settings: Settings) -> int:
    return max(400, int(target_words * settings.writer_min_words_ratio))


def _apply_writer_quality_loop(
    *,
    gateway: LLMGateway,
    writer_messages: list[dict[str, Any]],
    writer_out: WriterLLMOutput,
    chapter: ChapterRow,
    min_words: int,
    num_predict: int,
    settings: Settings,
    context_manifest: dict[str, Any],
) -> WriterLLMOutput:
    """Assess draft; expand or rewrite until quality_ok or repair budget exhausted."""
    max_repair = max(0, settings.writer_max_repair)
    expanded_count = 0
    rewritten_count = 0
    repair_attempts = 0
    quality = assess_draft_quality(
        writer_out.content,
        min_words,
        repair_repetition=settings.writer_repair_repetition,
        max_trim_ratio=settings.writer_max_trim_ratio,
    )
    writer_out = writer_out.model_copy(update={"content": quality.text})

    while not quality.quality_ok and repair_attempts < max_repair:
        repair_attempts += 1
        if quality.repetition_severe or (quality.repetition_truncated and quality.too_short):
            rewritten_count += 1
            rewrite_messages = writer_messages + [
                {
                    "role": "user",
                    "content": (
                        "上一稿出现大段重复或去重后不可用。请重写本章 content："
                        f"至少 {min_words} 字，目标约 {chapter.target_words} 字；"
                        f"紧扣剧情目标「{chapter.goal}」与硬设定中的人物/地点；"
                        "情节连贯推进，禁止任何复读/循环句段。"
                        "只输出合法 JSON（title/content/scene_summary）。"
                    ),
                }
            ]
            writer_out, _ = gateway.chat_structured(
                rewrite_messages,
                WriterLLMOutput,
                temperature=0.65,
                num_predict=num_predict,
            )
        elif quality.too_short:
            expanded_count += 1
            expand_messages = writer_messages + [
                {
                    "role": "assistant",
                    "content": writer_out.model_dump_json(),
                },
                {
                    "role": "user",
                    "content": (
                        f"正文过短（当前 {len(writer_out.content)} 字，要求至少 {min_words} 字，"
                        f"目标约 {chapter.target_words} 字）。请扩写 content，保持情节一致，"
                        f"紧扣剧情目标「{chapter.goal}」与硬设定人物/地点，"
                        "增加新的场景与对话，严禁重复已有句子或段落。"
                        "只输出合法 JSON（title/content/scene_summary）。"
                    ),
                },
            ]
            writer_out, _ = gateway.chat_structured(
                expand_messages,
                WriterLLMOutput,
                temperature=0.7,
                num_predict=num_predict,
            )
        else:
            break

        quality = assess_draft_quality(
            writer_out.content,
            min_words,
            repair_repetition=settings.writer_repair_repetition,
            max_trim_ratio=settings.writer_max_trim_ratio,
        )
        writer_out = writer_out.model_copy(update={"content": quality.text})

    context_manifest["repair_attempts"] = repair_attempts
    context_manifest["expanded"] = expanded_count > 0
    context_manifest["expanded_count"] = expanded_count
    context_manifest["repetition_truncated"] = quality.repetition_truncated
    context_manifest["repetition_severe"] = quality.repetition_severe
    context_manifest["repetition_rewritten"] = rewritten_count > 0
    context_manifest["rewritten_count"] = rewritten_count
    context_manifest["cut_ratio"] = round(quality.cut_ratio, 4)
    if quality.repetition_truncated:
        context_manifest["repetition_reason"] = quality.reason
        context_manifest["repetition_original_len"] = quality.original_len
        context_manifest["repetition_final_len"] = quality.final_len
    context_manifest["quality_ok"] = quality.quality_ok
    context_manifest["quality_reason"] = quality.reason
    context_manifest["final_word_count"] = quality.word_count
    return writer_out


def _validate_chapter_draft(
    gateway: LLMGateway,
    *,
    canon: str,
    chapter_title: str,
    chapter_goal: str,
    content: str,
    previous_summaries: str,
    settings: Settings,
) -> tuple[list[ChapterValidationIssue], str]:
    """Run LLM consistency check: OOC, timeline, foreshadowing, setting, logic."""
    registry = PromptRegistry(settings=settings)
    try:
        validator_spec, validator_messages = registry.render(
            "validator/chapter_check.yaml",
            canon=canon,
            chapter_title=chapter_title,
            chapter_goal=chapter_goal,
            content=content,
            previous_summaries=previous_summaries,
        )
        check_out, _ = gateway.chat_structured(
            validator_messages,
            ChapterCheckLLMOutput,
            temperature=validator_spec.default_params.get("temperature", 0.3),
            num_predict=validator_spec.default_params.get("num_predict", 1536),
        )
        issues: list[ChapterValidationIssue] = []
        for item in check_out.issues:
            issues.append(
                ChapterValidationIssue(
                    severity=item.severity,
                    category=item.category,
                    subject=item.subject,
                    description=item.description,
                    suggestion=item.suggestion or "",
                )
            )
        return issues, check_out.overall_assessment or ""
    except Exception:
        # Validation is advisory — never block the pipeline on its own failure
        return [], "（一致性检查未完成）"


def polish_chapter_draft(
    session: Session,
    chapter_id: str,
    *,
    gateway: LLMGateway | None = None,
    settings: Settings | None = None,
) -> GenerateChapterResult:
    """Polish an existing chapter draft — improve prose without changing plot."""
    settings = settings or get_settings()
    gateway = gateway or LLMGateway(settings=settings)

    chapter = session.get(ChapterRow, chapter_id)
    if chapter is None:
        raise NotFoundError(f"chapter not found: {chapter_id}", code="CHAPTER_NOT_FOUND")
    if not chapter.current_version_id:
        raise PreconditionError("chapter has no draft to polish", code="NO_DRAFT")
    book = _get_book(session, chapter.book_id)

    old_version = session.get(ChapterVersionRow, chapter.current_version_id)
    if old_version is None:
        raise NotFoundError("chapter version missing", code="VERSION_NOT_FOUND")
    was_confirmed = chapter.status == "confirmed" or old_version.status == "CONFIRMED"

    # Build context (same as generate_chapter)
    canon, context_manifest = _build_chapter_context(session, book, chapter, settings=settings)

    # Build previous summaries for the polish prompt
    prev_summaries_parts: list[str] = []
    confirmed_chs = list(
        session.scalars(
            select(ChapterRow)
            .where(ChapterRow.book_id == book.id, ChapterRow.status == "confirmed")
            .order_by(ChapterRow.number.desc())
        ).all()
    )
    for ch in list(reversed(confirmed_chs[:3])):
        if ch.current_version_id:
            ver = session.get(ChapterVersionRow, ch.current_version_id)
            if ver and ver.summary:
                prev_summaries_parts.append(f"第{ch.number}章《{ch.title}》：{ver.summary}")
    prev_summaries_text = "\n".join(prev_summaries_parts) if prev_summaries_parts else "（无前文章节）"

    # ── Step 1: run consistency check to find specific issues ──
    val_issues_before, val_summary_before = _validate_chapter_draft(
        gateway,
        canon=canon,
        chapter_title=old_version.title or chapter.title,
        chapter_goal=chapter.goal or "",
        content=old_version.content,
        previous_summaries=prev_summaries_text,
        settings=settings,
    )

    # Build numbered, actionable issue list for the prompt
    has_issues = len(val_issues_before) > 0
    if has_issues:
        issue_lines = []
        for i, vi in enumerate(val_issues_before, 1):
            sev_label = {"error": "严重", "warning": "建议", "info": "提醒"}.get(vi.severity, "")
            issue_lines.append(
                f"**问题 {i}** [{vi.category}] [{sev_label}] {vi.subject}：{vi.description}"
            )
            if vi.suggestion:
                issue_lines.append(f"  → 修复方式：{vi.suggestion}")
            else:
                issue_lines.append(f"  → 修复方式：请根据硬设定和上下文自行修正")
        issues_text = "\n\n".join(issue_lines)
    else:
        issues_text = ""

    task = TaskRow(
        id=new_id(),
        book_id=book.id,
        chapter_id=chapter.id,
        kind="chapter.polish",
        status=TaskStatus.RUNNING.value,
    )
    session.add(task)
    session.flush()

    registry = PromptRegistry(settings=settings)
    try:
        polish_spec, polish_messages = registry.render(
            "writer/chapter_polish.yaml",
            canon=canon,
            chapter_title=old_version.title or chapter.title,
            chapter_goal=chapter.goal or "",
            draft_content=old_version.content,
            previous_summaries=prev_summaries_text,
            validation_issues=issues_text,
            has_issues=has_issues,
            issue_count=len(val_issues_before),
        )
        num_predict = _writer_num_predict(
            chapter.target_words, settings,
            floor_from_prompt=polish_spec.default_params.get("num_predict"),
        )
        polish_out, _ = gateway.chat_structured(
            polish_messages,
            WriterLLMOutput,
            temperature=polish_spec.default_params.get("temperature", 0.6),
            num_predict=num_predict,
        )

        # Create new version with polished content
        version = ChapterVersionRow(
            id=new_id(),
            chapter_id=chapter.id,
            title=polish_out.title or chapter.title,
            content=polish_out.content,
            summary=polish_out.scene_summary or old_version.summary or "",
            status="CONFIRMED" if was_confirmed else "DRAFT",
            word_count=len(polish_out.content),
        )
        session.add(version)
        session.flush()

        # Extractor
        extractor_spec, extractor_messages = registry.render(
            "extractor/candidate_changes.yaml",
            chapter_id=chapter.id,
            canon=canon,
            content=version.content,
        )
        extractor_out, _ = gateway.chat_structured(
            extractor_messages,
            ExtractorLLMOutput,
            temperature=extractor_spec.default_params.get("temperature", 0.2),
            num_predict=extractor_spec.default_params.get("num_predict"),
        )
        change_set = CandidateChangeSetRow(
            id=new_id(),
            book_id=book.id,
            chapter_id=chapter.id,
            chapter_version_id=version.id,
            status="PROPOSED",
        )
        session.add(change_set)
        session.flush()
        change_outs: list[CandidateChangeOut] = []
        for item in extractor_out.changes:
            crow = CandidateChangeRow(
                id=new_id(),
                change_set_id=change_set.id,
                kind=item.kind.value if hasattr(item.kind, "value") else str(item.kind),
                subject=item.subject,
                claim=item.claim,
                evidence_quote=item.evidence_quote,
                confidence=item.confidence,
                status="PROPOSED",
            )
            session.add(crow)
            change_outs.append(
                CandidateChangeOut(
                    id=crow.id, kind=crow.kind, subject=crow.subject,
                    claim=crow.claim, evidence_quote=crow.evidence_quote,
                    confidence=crow.confidence, status=crow.status,
                )
            )

        chapter.current_version_id = version.id
        chapter.title = version.title
        chapter.status = "confirmed" if was_confirmed else "drafted"
        book.version += 1

        # Validation
        val_issues, val_summary = _validate_chapter_draft(
            gateway, canon=canon, chapter_title=version.title,
            chapter_goal=chapter.goal or "", content=version.content,
            previous_summaries=prev_summaries_text, settings=settings,
        )
        context_manifest["polished"] = True
        context_manifest["original_word_count"] = old_version.word_count
        context_manifest["polished_word_count"] = version.word_count
        context_manifest["issues_before_fix"] = len(val_issues_before)
        context_manifest["issues_after_fix"] = len(val_issues)
        context_manifest["validation_issue_count"] = len(val_issues)
        context_manifest["before_issues"] = [
            {"severity": vi.severity, "category": vi.category,
             "subject": vi.subject, "description": vi.description,
             "suggestion": vi.suggestion}
            for vi in val_issues_before
        ]

        result = GenerateChapterResult(
            task_id=task.id,
            chapter=_chapter_out(chapter),
            version=ChapterVersionOut(
                id=version.id, chapter_id=chapter.id, title=version.title,
                content=version.content, summary=version.summary,
                status=version.status, word_count=version.word_count,
            ),
            changes=change_outs,
            change_set_id=change_set.id,
            context_manifest=context_manifest,
            validation_issues=val_issues,
            validation_summary=val_summary,
        )
        task.status = TaskStatus.SUCCEEDED.value
        task.result_json = result.model_dump_json()
        session.flush()

        # Auto-save
        settings.export_dir.mkdir(parents=True, exist_ok=True)
        safe_title = "".join(
            ch for ch in book.title if ch.isalnum() or ch in ("-", "_", " ")
        ).strip().replace(" ", "_") or "book"
        ch_path = (
            settings.export_dir
            / f"{book.id[:8]}_{safe_title}_ch{chapter.number:02d}.txt"
        )
        ch_path.write_text(version.content, encoding="utf-8")

        return result
    except Exception as exc:
        task.status = TaskStatus.FAILED.value
        task.error = str(exc)
        raise


def generate_chapter(
    session: Session,
    chapter_id: str,
    *,
    gateway: LLMGateway | None = None,
    settings: Settings | None = None,
    idempotency_key: str | None = None,
) -> GenerateChapterResult:
    settings = settings or get_settings()
    gateway = gateway or LLMGateway(settings=settings)

    if idempotency_key:
        existing = session.scalars(
            select(TaskRow).where(TaskRow.idempotency_key == idempotency_key)
        ).first()
        if existing and existing.status == TaskStatus.SUCCEEDED.value and existing.result_json:
            data = json.loads(existing.result_json)
            return GenerateChapterResult.model_validate(data)

    chapter = session.get(ChapterRow, chapter_id)
    if chapter is None:
        raise NotFoundError(f"chapter not found: {chapter_id}", code="CHAPTER_NOT_FOUND")
    book = _get_book(session, chapter.book_id)
    if not book.characters_locked:
        raise PreconditionError("characters must be locked", code="CHARACTERS_NOT_LOCKED")
    _ensure_chapter_sequence_ok(session, chapter)

    canon, context_manifest = _build_chapter_context(
        session, book, chapter, settings=settings
    )

    task = TaskRow(
        id=new_id(),
        book_id=book.id,
        chapter_id=chapter.id,
        kind="chapter.generate",
        status=TaskStatus.RUNNING.value,
        idempotency_key=idempotency_key,
    )
    session.add(task)
    session.flush()

    registry = PromptRegistry(settings=settings)
    min_words = _min_words_for_chapter(chapter.target_words, settings)
    try:
        writer_spec, writer_messages = registry.render(
            "writer/chapter_draft.yaml",
            canon=canon,
            chapter_id=chapter.id,
            chapter_title=chapter.title,
            target_words=chapter.target_words,
            min_words=min_words,
            chapter_goal=chapter.goal,
        )
        num_predict = _writer_num_predict(
            chapter.target_words,
            settings,
            floor_from_prompt=writer_spec.default_params.get("num_predict"),
        )
        context_manifest["target_words"] = chapter.target_words
        context_manifest["min_words"] = min_words
        context_manifest["num_predict"] = num_predict

        writer_out, _ = gateway.chat_structured(
            writer_messages,
            WriterLLMOutput,
            temperature=writer_spec.default_params.get("temperature", 0.75),
            num_predict=num_predict,
        )
        writer_out = _apply_writer_quality_loop(
            gateway=gateway,
            writer_messages=writer_messages,
            writer_out=writer_out,
            chapter=chapter,
            min_words=min_words,
            num_predict=num_predict,
            settings=settings,
            context_manifest=context_manifest,
        )

        version = ChapterVersionRow(
            id=new_id(),
            chapter_id=chapter.id,
            title=writer_out.title or chapter.title,
            content=writer_out.content,
            summary=writer_out.scene_summary or "",
            status="DRAFT",
            word_count=len(writer_out.content),
        )
        session.add(version)
        session.flush()

        extractor_spec, extractor_messages = registry.render(
            "extractor/candidate_changes.yaml",
            chapter_id=chapter.id,
            canon=canon,
            content=version.content,
        )
        extractor_out, _ = gateway.chat_structured(
            extractor_messages,
            ExtractorLLMOutput,
            temperature=extractor_spec.default_params.get("temperature", 0.2),
            num_predict=extractor_spec.default_params.get("num_predict"),
        )
        change_set = CandidateChangeSetRow(
            id=new_id(),
            book_id=book.id,
            chapter_id=chapter.id,
            chapter_version_id=version.id,
            status="PROPOSED",
        )
        session.add(change_set)
        session.flush()
        change_outs: list[CandidateChangeOut] = []
        for item in extractor_out.changes:
            crow = CandidateChangeRow(
                id=new_id(),
                change_set_id=change_set.id,
                kind=item.kind.value if hasattr(item.kind, "value") else str(item.kind),
                subject=item.subject,
                claim=item.claim,
                evidence_quote=item.evidence_quote,
                confidence=item.confidence,
                status="PROPOSED",
            )
            session.add(crow)
            change_outs.append(
                CandidateChangeOut(
                    id=crow.id,
                    kind=crow.kind,
                    subject=crow.subject,
                    claim=crow.claim,
                    evidence_quote=crow.evidence_quote,
                    confidence=crow.confidence,
                    status=crow.status,
                )
            )

        chapter.current_version_id = version.id
        chapter.title = version.title
        chapter.status = "drafted"
        book.version += 1

        # ── consistency validation ──
        prev_summaries_parts: list[str] = []
        confirmed_chs = list(
            session.scalars(
                select(ChapterRow)
                .where(ChapterRow.book_id == book.id, ChapterRow.status == "confirmed")
                .order_by(ChapterRow.number.desc())
            ).all()
        )
        for ch in list(reversed(confirmed_chs[:3])):
            if ch.current_version_id:
                ver = session.get(ChapterVersionRow, ch.current_version_id)
                if ver and ver.summary:
                    prev_summaries_parts.append(f"第{ch.number}章《{ch.title}》：{ver.summary}")
        prev_summaries_text = "\n".join(prev_summaries_parts) if prev_summaries_parts else "（无前文章节）"

        val_issues, val_summary = _validate_chapter_draft(
            gateway,
            canon=canon,
            chapter_title=version.title,
            chapter_goal=chapter.goal or "",
            content=version.content,
            previous_summaries=prev_summaries_text,
            settings=settings,
        )
        context_manifest["validation_issue_count"] = len(val_issues)

        result = GenerateChapterResult(
            task_id=task.id,
            chapter=_chapter_out(chapter),
            version=ChapterVersionOut(
                id=version.id,
                chapter_id=chapter.id,
                title=version.title,
                content=version.content,
                summary=version.summary,
                status=version.status,
                word_count=version.word_count,
            ),
            changes=change_outs,
            change_set_id=change_set.id,
            context_manifest=context_manifest,
            validation_issues=val_issues,
            validation_summary=val_summary,
        )
        task.status = TaskStatus.SUCCEEDED.value
        task.result_json = result.model_dump_json()
        session.flush()

        # ── auto-save chapter content to exports dir ──
        settings.export_dir.mkdir(parents=True, exist_ok=True)
        safe_title = "".join(
            ch for ch in book.title if ch.isalnum() or ch in ("-", "_", " ")
        ).strip().replace(" ", "_") or "book"
        ch_path = (
            settings.export_dir
            / f"{book.id[:8]}_{safe_title}_ch{chapter.number:02d}.txt"
        )
        ch_path.write_text(version.content, encoding="utf-8")

        return result
    except Exception as exc:  # noqa: BLE001
        task.status = TaskStatus.FAILED.value
        task.error = str(exc)
        raise


def confirm_chapter(
    session: Session,
    chapter_id: str,
    *,
    force: bool = False,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    chapter = session.get(ChapterRow, chapter_id)
    if chapter is None:
        raise NotFoundError(f"chapter not found: {chapter_id}", code="CHAPTER_NOT_FOUND")
    _ensure_chapter_sequence_ok(session, chapter)
    if not chapter.current_version_id:
        raise PreconditionError("chapter has no draft", code="NO_DRAFT")
    version = session.get(ChapterVersionRow, chapter.current_version_id)
    if version is None:
        raise NotFoundError("chapter version missing", code="VERSION_NOT_FOUND")
    if version.status == "CONFIRMED":
        return {"chapter_id": chapter_id, "status": "already_confirmed", "facts_added": 0}

    if settings.writer_enforce_quality_on_confirm and not force:
        min_words = _min_words_for_chapter(chapter.target_words, settings)
        quality = assess_draft_quality(
            version.content,
            min_words,
            repair_repetition=settings.writer_repair_repetition,
            max_trim_ratio=settings.writer_max_trim_ratio,
        )
        if not quality.quality_ok:
            raise PreconditionError(
                "chapter draft failed quality gate (too short or severe repetition)",
                code="CHAPTER_QUALITY_FAILED",
                details={
                    "chapter_id": chapter_id,
                    "word_count": quality.word_count,
                    "min_words": min_words,
                    "target_words": chapter.target_words,
                    "reason": quality.reason,
                    "cut_ratio": round(quality.cut_ratio, 4),
                    "hint": "Regenerate the chapter, or confirm with force=true to override",
                },
            )

    change_set = session.scalars(
        select(CandidateChangeSetRow).where(
            CandidateChangeSetRow.chapter_version_id == version.id,
            CandidateChangeSetRow.status == "PROPOSED",
        )
    ).first()

    facts_added = 0
    if change_set:
        changes = session.scalars(
            select(CandidateChangeRow).where(CandidateChangeRow.change_set_id == change_set.id)
        ).all()
        for c in changes:
            fact = ConfirmedFactRow(
                id=new_id(),
                book_id=chapter.book_id,
                chapter_id=chapter.id,
                source_change_id=c.id,
                kind=c.kind,
                subject=c.subject,
                claim=c.claim,
                evidence_quote=c.evidence_quote,
                status="CONFIRMED",
            )
            session.add(fact)
            c.status = "CONFIRMED"
            upsert_fts(
                session,
                doc_id=f"fact:{fact.id}",
                book_id=chapter.book_id,
                doc_type="fact",
                title=c.subject,
                body=c.claim,
            )
            facts_added += 1
        change_set.status = "CONFIRMED"

    version.status = "CONFIRMED"
    chapter.status = "confirmed"
    upsert_fts(
        session,
        doc_id=f"chapter:{version.id}",
        book_id=chapter.book_id,
        doc_type="chapter_summary",
        title=version.title,
        body=version.summary or version.content[:500],
    )
    book = _get_book(session, chapter.book_id)
    book.version += 1
    session.flush()
    return {
        "chapter_id": chapter_id,
        "version_id": version.id,
        "status": "confirmed",
        "facts_added": facts_added,
    }


def get_task(session: Session, task_id: str) -> TaskOut:
    row = session.get(TaskRow, task_id)
    if row is None:
        raise NotFoundError(f"task not found: {task_id}", code="TASK_NOT_FOUND")
    return TaskOut(
        id=row.id,
        kind=row.kind,
        status=row.status,
        book_id=row.book_id,
        chapter_id=row.chapter_id,
        error=row.error,
    )


def count_confirmed_facts(session: Session, book_id: str) -> int:
    return len(
        session.scalars(select(ConfirmedFactRow).where(ConfirmedFactRow.book_id == book_id)).all()
    )


def _assemble_book_text(
    session: Session,
    book: BookRow,
    *,
    confirmed_only: bool = False,
) -> tuple[str, int, str, int, int]:
    """Return (full_text, total_chars, summary, exported_chapter_count, planned_count)."""
    chapters = list(
        session.scalars(
            select(ChapterRow).where(ChapterRow.book_id == book.id).order_by(ChapterRow.number)
        ).all()
    )
    planned_count = len(chapters)
    if not chapters:
        raise PreconditionError("no chapters planned", code="NO_CHAPTERS")

    if confirmed_only:
        to_export = [c for c in chapters if c.status == "confirmed" and c.current_version_id]
        if not to_export:
            raise PreconditionError(
                "no confirmed chapters to export",
                code="NO_CONFIRMED_CHAPTERS",
            )
    else:
        unfinished = [c for c in chapters if c.status != "confirmed"]
        if unfinished:
            raise PreconditionError(
                "not all chapters are confirmed",
                code="BOOK_INCOMPLETE",
                details={
                    "unfinished": [
                        {"id": c.id, "number": c.number, "status": c.status} for c in unfinished
                    ]
                },
            )
        to_export = chapters

    scope_note = (
        f"导出范围：已确认 {len(to_export)}/{planned_count} 章（部分导出）"
        if confirmed_only and len(to_export) < planned_count
        else f"导出范围：全部 {len(to_export)} 章"
    )
    parts: list[str] = [
        book.title,
        "",
        f"类型：{book.genre}　风格：{book.style}　基调：{book.tone}",
        f"梗概：{book.premise}",
        scope_note,
        "",
        "========",
        "",
    ]
    summary_lines: list[str] = []
    total = 0
    for ch in to_export:
        ver = session.get(ChapterVersionRow, ch.current_version_id) if ch.current_version_id else None
        if ver is None or not ver.content:
            raise PreconditionError(
                f"chapter {ch.number} has no content",
                code="CHAPTER_EMPTY",
            )
        heading = f"第{ch.number}章 {ver.title or ch.title}"
        parts.append(heading)
        parts.append("")
        parts.append(ver.content.strip())
        parts.append("")
        parts.append("--------")
        parts.append("")
        total += len(ver.content)
        if ver.summary:
            summary_lines.append(f"第{ch.number}章：{ver.summary}")
    summary = (
        "\n".join(summary_lines)
        if summary_lines
        else f"《{book.title}》导出 {len(to_export)}/{planned_count} 章。"
    )
    return "\n".join(parts).rstrip() + "\n", total, summary, len(to_export), planned_count


def complete_book(session: Session, book_id: str) -> CompleteBookResult:
    book = _get_book(session, book_id)
    chapters = list(
        session.scalars(
            select(ChapterRow).where(ChapterRow.book_id == book_id).order_by(ChapterRow.number)
        ).all()
    )
    if not chapters:
        raise PreconditionError("no chapters to complete", code="NO_CHAPTERS")
    unfinished = [c for c in chapters if c.status != "confirmed"]
    if unfinished:
        raise PreconditionError(
            "not all chapters are confirmed",
            code="BOOK_INCOMPLETE",
            details={
                "unfinished": [
                    {"id": c.id, "number": c.number, "status": c.status} for c in unfinished
                ]
            },
        )
    _text, total_chars, summary, chapter_count, _planned = _assemble_book_text(
        session, book, confirmed_only=False
    )
    book.status = "completed"
    book.version += 1
    session.flush()
    return CompleteBookResult(
        book_id=book.id,
        status=book.status,
        chapter_count=chapter_count,
        total_chars=total_chars,
        summary=summary,
    )


def export_book_txt(
    session: Session,
    book_id: str,
    *,
    settings: Settings | None = None,
    scope: str = "confirmed",
) -> ExportBookResult:
    """Export book text. scope=confirmed|all (all requires every chapter confirmed)."""
    from novel_agent.bootstrap import ensure_data_dirs

    if scope not in {"confirmed", "all"}:
        raise PreconditionError("scope must be confirmed or all", code="EXPORT_SCOPE")

    settings = ensure_data_dirs(settings or get_settings())
    book = _get_book(session, book_id)
    confirmed_only = scope == "confirmed"
    text, total_chars, _summary, exported_count, planned_count = _assemble_book_text(
        session, book, confirmed_only=confirmed_only
    )
    safe_title = "".join(ch for ch in book.title if ch.isalnum() or ch in ("-", "_", " "))
    safe_title = safe_title.strip().replace(" ", "_") or "book"
    suffix = "partial" if confirmed_only and exported_count < planned_count else "full"
    path = settings.export_dir / f"{book.id[:8]}_{safe_title}_{suffix}.txt"
    path.write_text(text, encoding="utf-8")
    return ExportBookResult(
        book_id=book.id,
        format="txt",
        path=str(path.resolve()),
        chars=total_chars,
        scope=scope,
        chapter_count=exported_count,
        planned_chapter_count=planned_count,
    )


def generate_full_book(
    session: Session,
    book_id: str,
    *,
    gateway: LLMGateway | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """One-click full pipeline: characters → outline → chapters → write all → complete → export.
    Returns dict with status updates and final export path."""
    settings = settings or get_settings()
    gateway = gateway or LLMGateway(settings=settings)
    log: list[str] = []

    def _report(msg: str) -> None:
        log.append(msg)

    try:
        _report("📝 生成人物...")
        generate_characters(session, book_id, gateway=gateway, settings=settings)
        session.commit()
        _report("✅ 人物生成完成")

        _report("🔒 锁定人物...")
        lock_characters(session, book_id)
        session.commit()
        _report("✅ 人物已锁定")

        _report("📋 生成大纲...")
        generate_outline(session, book_id, gateway=gateway, settings=settings)
        session.commit()
        _report("✅ 大纲生成完成")

        _report("🔒 锁定大纲...")
        lock_outline(session, book_id)
        session.commit()
        _report("✅ 大纲已锁定")

        _report("📊 章节规划...")
        chapters = plan_chapters(session, book_id, settings=settings)
        session.commit()
        _report(f"✅ 共规划 {len(chapters)} 章")

        for i, ch in enumerate(chapters):
            _report(f"✍️ 写作第 {i+1}/{len(chapters)} 章《{ch.title}》...")
            generate_chapter(session, ch.id, gateway=gateway, settings=settings)
            session.commit()
            confirm_chapter(session, ch.id, force=True, settings=settings)
            session.commit()
            _report(f"✅ 第 {i+1}/{len(chapters)} 章完成")

        _report("🏁 完本...")
        complete_book(session, book_id)
        session.commit()
        _report("✅ 完本")

        _report("📥 导出...")
        export_result = export_book_txt(session, book_id, scope="all", settings=settings)
        session.commit()
        _report(f"✅ 导出至 {export_result.path}")

        return {
            "success": True,
            "log": log,
            "path": export_result.path,
            "chars": export_result.chars,
            "chapter_count": export_result.chapter_count,
        }
    except Exception as exc:
        session.rollback()
        _report(f"❌ 失败：{exc}")
        return {"success": False, "log": log, "error": str(exc)}


def import_custom_outline(
    session: Session,
    book_id: str,
    outline_text: str,
    *,
    settings: Settings | None = None,
) -> list[OutlineNodeOut]:
    """Parse user-provided outline text and create outline nodes.
    Supports two formats:
    1. Markdown headings: # vision, ## arc, ### chapter_goal
    2. Plain text with indentation or numbered lines
    """
    settings = settings or get_settings()
    book = _get_book(session, book_id)
    if book.outline_locked:
        raise PreconditionError("outline is locked", code="OUTLINE_LOCKED")

    # Clear existing nodes
    _delete_book_chapters(session, book_id)
    _delete_book_outline(session, book_id)

    lines = [l.strip() for l in outline_text.strip().split("\n") if l.strip()]
    if not lines:
        raise PreconditionError("outline text is empty", code="OUTLINE_EMPTY")

    # Detect format: markdown headings vs plain text
    has_md_headings = any(l.startswith("#") for l in lines)

    nodes: list[OutlineNodeRow] = []
    if has_md_headings:
        # Parse markdown headings
        current_vision: OutlineNodeRow | None = None
        current_arc: OutlineNodeRow | None = None
        vision_order = 0
        arc_order = 0

        for line in lines:
            if line.startswith("### ") or line.startswith("###"):
                title = line.lstrip("#").strip()
                if not title:
                    continue
                parent_id = current_arc.id if current_arc else (current_vision.id if current_vision else None)
                node = OutlineNodeRow(
                    id=new_id(), book_id=book_id, parent_id=parent_id,
                    level="chapter_goal", title=title, summary="",
                    sort_order=len(nodes), locked=False, version=1,
                )
                session.add(node)
                nodes.append(node)
            elif line.startswith("## ") or line.startswith("##"):
                title = line.lstrip("#").strip()
                if not title:
                    continue
                arc_order += 1
                node = OutlineNodeRow(
                    id=new_id(), book_id=book_id,
                    parent_id=current_vision.id if current_vision else None,
                    level="arc", title=title, summary="",
                    sort_order=arc_order, locked=False, version=1,
                )
                session.add(node)
                nodes.append(node)
                current_arc = node
            elif line.startswith("# ") or line.startswith("#"):
                title = line.lstrip("#").strip()
                if not title:
                    continue
                vision_order += 1
                node = OutlineNodeRow(
                    id=new_id(), book_id=book_id, parent_id=None,
                    level="vision", title=title, summary="",
                    sort_order=vision_order, locked=False, version=1,
                )
                session.add(node)
                nodes.append(node)
                current_vision = node
                current_arc = None
    else:
        # Plain text: treat each non-empty line as a chapter_goal, group by blank lines as arcs
        vision = OutlineNodeRow(
            id=new_id(), book_id=book_id, parent_id=None,
            level="vision", title=book.title or "大纲", summary="",
            sort_order=0, locked=False, version=1,
        )
        session.add(vision)
        nodes.append(vision)

        current_arc = None
        arc_idx = 0
        for line in lines:
            # Arc headers: "第N卷/部/篇" or short lines containing 卷/部/篇.
            # Genuine chapter headings ("第一章/第X回") must NOT become an arc.
            if _ARC_HEADER_RE.match(line) or (
                len(line) <= 30
                and any(k in line for k in ("卷", "部", "篇"))
                and not _CHAPTER_HEADER_RE.match(line)
            ):
                arc_idx += 1
                current_arc = OutlineNodeRow(
                    id=new_id(), book_id=book_id, parent_id=vision.id,
                    level="arc", title=line, summary="",
                    sort_order=arc_idx, locked=False, version=1,
                )
                session.add(current_arc)
                nodes.append(current_arc)
            else:
                # Treat as chapter_goal
                parent_id = current_arc.id if current_arc else vision.id
                node = OutlineNodeRow(
                    id=new_id(), book_id=book_id, parent_id=parent_id,
                    level="chapter_goal", title=line[:80], summary=line,
                    sort_order=len(nodes), locked=False, version=1,
                )
                session.add(node)
                nodes.append(node)

    session.flush()
    book.version += 1
    return [_outline_out(n) for n in nodes]
