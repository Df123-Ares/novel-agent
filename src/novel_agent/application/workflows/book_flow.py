"""Book / outline / character / chapter workflows for phase-1 MVP."""

from __future__ import annotations

import json
import math
import re
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from novel_agent.domain.book.llm_outputs import (
    CharactersLLMOutput,
    OutlineLLMOutput,
    OutlineNodeLLM,
)
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
from novel_agent.domain.errors import (
    ConflictError,
    NotFoundError,
    PreconditionError,
    new_id,
)
from novel_agent.domain.story.llm_outputs import (
    ChapterCheckLLMOutput,
    ExtractorLLMOutput,
    WriterLLMOutput,
)
from novel_agent.domain.tasks import TaskStatus
from novel_agent.domain.text_quality import assess_draft_quality
from novel_agent.infrastructure.llm.gateway import LLMGateway
from novel_agent.infrastructure.llm.stats import record_gate
from novel_agent.infrastructure.persistence.fts import search_relevant_facts, upsert_fts
from novel_agent.infrastructure.persistence.models import (
    ArcSummaryRow,
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


def _persist_failed_task(
    session: Session,
    task_id: str,
    book_id: str,
    kind: str,
    error: str,
    *,
    chapter_id: str | None = None,
    idempotency_key: str | None = None,
) -> None:
    """Rollback all pending changes, re-create just the failed task, and commit it.

    Without this helper, a task marked FAILED inside an except block gets rolled
    back together with the partial data (versions, change sets, ...), leaving a
    zombie RUNNING task in the DB that the caller's rollback never sees.
    """
    session.rollback()
    session.expunge_all()
    failed_task = TaskRow(
        id=task_id,
        book_id=book_id,
        chapter_id=chapter_id,
        kind=kind,
        status=TaskStatus.FAILED.value,
        error=error,
        idempotency_key=idempotency_key,
    )
    session.add(failed_task)
    session.commit()


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
    _counter: list[int] | None = None,
) -> None:
    if _counter is None:
        _counter = [0]
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
    for child in node.children:
        _counter[0] += 1
        _persist_outline_tree(session, book_id, child, row.id, _counter[0], _counter)


def generate_outline(
    session: Session,
    book_id: str,
    *,
    gateway: LLMGateway | None = None,
    settings: Settings | None = None,
    progress_callback: Callable[[str, str], None] | None = None,
) -> list[OutlineNodeOut]:
    settings = settings or get_settings()
    gateway = gateway or LLMGateway(settings=settings)
    book = _get_book(session, book_id)

    def _emit(stage: str, msg: str) -> None:
        if progress_callback:
            progress_callback(stage, msg)

    if book.outline_locked:
        raise PreconditionError("outline is locked", code="OUTLINE_LOCKED")

    _emit("init", "清空旧大纲与章节...")
    # clear existing nodes / dependent chapters
    _delete_book_chapters(session, book_id)
    _delete_book_outline(session, book_id)

    # ── dynamic chapter count by length ──
    _length_ranges = {
        "short": {"min_arcs": 1, "max_arcs": 2, "min_ch": 2, "max_ch": 14},
        "medium": {"min_arcs": 2, "max_arcs": 4, "min_ch": 3, "max_ch": 50},
        "long": {"min_arcs": 3, "max_arcs": 6, "min_ch": 4, "max_ch": 120},
    }
    lr = _length_ranges.get(book.length, _length_ranges["medium"])

    _emit("context", "组装大纲提示词（含已锁定人物）...")
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
        _emit("generating", f"LLM 生成大纲（预计 {lr['min_arcs']}-{lr['max_arcs']} 卷 · 预计 40-90 秒）...")
        out, _ = gateway.chat_structured(
            messages,
            OutlineLLMOutput,
            temperature=spec.default_params.get("temperature", 0.5),
            num_predict=spec.default_params.get("num_predict"),
        )
        _emit("persisting", "保存大纲节点到数据库...")
        _persist_outline_tree(session, book_id, out.root, None, 0)
        book.version += 1
        task.status = TaskStatus.SUCCEEDED.value
        task.result_json = json.dumps({"prompt": spec.prompt_id, "version": spec.version})
    except Exception as exc:
        _persist_failed_task(
            session, task.id, book_id, "outline.generate", str(exc),
        )
        raise

    nodes = session.scalars(
        select(OutlineNodeRow).where(OutlineNodeRow.book_id == book_id)
        .order_by(OutlineNodeRow.sort_order)
    ).all()
    _emit("done", f"大纲生成完成：{len(nodes)} 个节点")
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
    progress_callback: Callable[[str, str], None] | None = None,
) -> list[CharacterOut]:
    settings = settings or get_settings()
    gateway = gateway or LLMGateway(settings=settings)
    book = _get_book(session, book_id)

    def _emit(stage: str, msg: str) -> None:
        if progress_callback:
            progress_callback(stage, msg)

    if book.characters_locked:
        raise PreconditionError("characters are locked", code="CHARACTERS_LOCKED")

    _emit("init", "清空旧人物数据...")
    for c in session.scalars(select(CharacterRow).where(CharacterRow.book_id == book_id)).all():
        session.delete(c)
    session.flush()

    _emit("context", "组装人物提示词...")
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
        _emit("generating", "LLM 生成人物设计（主角+配角 · 预计 30-60 秒）...")
        out, _ = gateway.chat_structured(
            messages,
            CharactersLLMOutput,
            temperature=spec.default_params.get("temperature", 0.5),
            num_predict=spec.default_params.get("num_predict"),
        )
        _emit("persisting", f"保存 {len(out.characters)} 个人物...")
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
    except Exception as exc:
        _persist_failed_task(
            session, task.id, book_id, "characters.generate", str(exc),
        )
        raise

    session.flush()
    rows = session.scalars(select(CharacterRow).where(CharacterRow.book_id == book_id)).all()
    _emit("done", f"人物生成完成：{len(rows)} 个人物")
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


def _clean_chapter_title(title: str) -> str:
    """Strip LLM-generated chapter numbering prefix like '第一章：' / '第1章 ' / 'Chapter 1'."""
    cleaned = re.sub(
        r"^第[零一二三四五六七八九十百千\d]+[章节回卷部篇](?:\s*[:：\-\s])?",
        "",
        title,
    )
    cleaned = re.sub(r"^Chapter\s*\d+\s*[:：\-\s]?", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip() or title


def plan_chapters(
    session: Session,
    book_id: str,
    *,
    settings: Settings | None = None,
    progress_callback: Callable[[str, str], None] | None = None,
) -> list[ChapterOut]:
    settings = settings or get_settings()
    book = _get_book(session, book_id)

    def _emit(stage: str, msg: str) -> None:
        if progress_callback:
            progress_callback(stage, msg)

    if not book.outline_locked:
        raise PreconditionError("outline must be locked first", code="OUTLINE_NOT_LOCKED")
    if not book.characters_locked:
        raise PreconditionError("characters must be locked first", code="CHARACTERS_NOT_LOCKED")

    _emit("init", "清空旧章节规划...")
    _delete_book_chapters(session, book_id)

    _emit("collect", "从大纲收集章节目标节点...")
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

    _emit("allocating", f"字数分配：共 {len(goals)} 个情节目标...")
    total = _length_total_words(settings, book.length)
    max_words = settings.max_chapter_words
    # allocate words per goal, then split if exceeds max_chapter_words
    alloc_per_goal = max(1200, total // len(goals))
    chapters: list[ChapterRow] = []
    for node in goals:
        goal_words = alloc_per_goal
        # if single goal alloc exceeds max_words, split into multiple chapters
        n_parts = max(1, math.ceil(goal_words / max_words))
        base = goal_words // n_parts
        rem = goal_words % n_parts
        for k in range(n_parts):
            words = base + (1 if k < rem else 0)
            title = _clean_chapter_title(node.title) if n_parts == 1 else f"{_clean_chapter_title(node.title)}（{k+1}）"
            goal_text = node.summary or node.title
            if n_parts > 1:
                goal_text = f"{goal_text}（本目标共{n_parts}章，本章为第{k+1}章，只推进本章对应情节段落）"
            row = ChapterRow(
                id=new_id(),
                book_id=book_id,
                outline_node_id=node.id,
                number=len(chapters) + 1,
                title=title,
                goal=goal_text,
                target_words=words,
                status="planned",
            )
            session.add(row)
            chapters.append(row)
    book.version += 1
    session.flush()
    _emit("done", f"章节规划完成：共 {len(chapters)} 章，总目标 {total} 字")
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
    """Assemble writing context + manifest for a chapter draft.

    Facts are retrieved via RAG (FTS5 bm25 on chapter goal/title), falling back
    to the most recent facts when RAG returns fewer than max_facts_in_context.
    This keeps long-novel context relevant: chapter 400 can still see chapter 50
    setting facts if they match the current goal, instead of only seeing the
    last 50 facts which may all be from chapters 350-399.
    """
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

    # ── RAG fact retrieval ──────────────────────────────────────────────────
    # Query from chapter goal + title so the model sees facts relevant to the
    # current scene, not just chronologically recent ones.
    rag_query = f"{chapter.title} {chapter.goal}".strip()
    relevant_fact_ids = search_relevant_facts(
        session, book.id, rag_query, limit=settings.max_facts_in_context
    )
    facts_by_id = {f.id: f for f in all_facts}
    rag_facts = [facts_by_id[fid] for fid in relevant_fact_ids if fid in facts_by_id]
    rag_ids = {f.id for f in rag_facts}

    # Fallback: fill remaining slots with most recent facts not in RAG results.
    # Preserves time-order awareness for facts RAG missed (e.g. very recent
    # plot developments that share no keywords with the current goal).
    remaining_slots = settings.max_facts_in_context - len(rag_facts)
    if remaining_slots > 0:
        recent_fallback = [
            f for f in reversed(all_facts) if f.id not in rag_ids
        ][:remaining_slots]
        facts = rag_facts + recent_fallback
    else:
        facts = rag_facts[: settings.max_facts_in_context]

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

    # ── 分层摘要注入（长程一致性核心）─────────────────────────────────────
    # 当存在 arc_summary 时，用最近 N 卷的摘要补充最近 3 章看不到的剧情。
    # 第 30 章能看到：第 1-10 卷摘要 + 第 11-20 卷摘要（最近 2 卷）。
    # 第 80 章能看到：第 1-50 大卷摘要 + 第 51-60 卷 + 第 61-70 卷 + 第 71-80 卷摘要。
    arc_summary_lines: list[str] = []
    arc_summary_ranges: list[dict[str, int]] = []
    if settings.arc_summaries_in_context > 0:
        # 当前章节所属的 arc_index 之前的所有 arc 摘要（不包含当前未完成的 arc）
        current_arc_index = (chapter.number - 1) // settings.arc_size + 1
        arc_rows = list(
            session.scalars(
                select(ArcSummaryRow)
                .where(
                    ArcSummaryRow.book_id == book.id,
                    ArcSummaryRow.level == "arc",
                    ArcSummaryRow.arc_index < current_arc_index,
                )
                .order_by(ArcSummaryRow.arc_index.desc())
                .limit(settings.arc_summaries_in_context)
            ).all()
        )
        # Reverse to chronological order for narrative coherence
        arc_rows.reverse()
        for r in arc_rows:
            arc_summary_lines.append(
                f"第{r.arc_index}卷（第{r.chapter_start}-{r.chapter_end}章）摘要：{r.summary}"
            )
            arc_summary_ranges.append(
                {"arc_index": r.arc_index, "start": r.chapter_start, "end": r.chapter_end}
            )

    mega_summary_lines: list[str] = []
    mega_summary_ranges: list[dict[str, int]] = []
    if settings.mega_arc_summaries_in_context > 0:
        current_mega_index = (chapter.number - 1) // settings.mega_arc_size + 1
        mega_rows = list(
            session.scalars(
                select(ArcSummaryRow)
                .where(
                    ArcSummaryRow.book_id == book.id,
                    ArcSummaryRow.level == "mega",
                    ArcSummaryRow.arc_index < current_mega_index,
                )
                .order_by(ArcSummaryRow.arc_index.desc())
                .limit(settings.mega_arc_summaries_in_context)
            ).all()
        )
        mega_rows.reverse()
        for r in mega_rows:
            mega_summary_lines.append(
                f"第{r.arc_index}大卷（第{r.chapter_start}-{r.chapter_end}章）摘要：{r.summary}"
            )
            mega_summary_ranges.append(
                {"mega_index": r.arc_index, "start": r.chapter_start, "end": r.chapter_end}
            )

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

    # ── few-shot 风格示例 ──────────────────────────────────────────────
    # 从已确认章节中抽取一段正文作为风格参考，帮助 LLM 保持文风一致。
    # 策略：优先取上一章开头（最新文风），若上一章无内容则取更早的确认章节。
    style_sample = ""
    style_sample_chapter: int | None = None
    sample_chars = settings.few_shot_sample_chars
    if sample_chars > 0 and chapter.number > 1:
        sample_chapter = session.scalars(
            select(ChapterRow).where(
                ChapterRow.book_id == book.id,
                ChapterRow.number == chapter.number - 1,
                ChapterRow.status == "confirmed",
            )
        ).first()
        if sample_chapter and sample_chapter.current_version_id:
            sample_ver = session.get(ChapterVersionRow, sample_chapter.current_version_id)
            if sample_ver and sample_ver.content:
                # 取正文开头（开头最能体现叙事基调与节奏）
                style_sample = sample_ver.content[:sample_chars]
                style_sample_chapter = sample_chapter.number

    canon = _build_canon(book, characters, facts)
    parts = [canon]
    # few-shot 风格示例放在 canon 之后、摘要之前（先建立风格锚点，再补充剧情上下文）
    if style_sample:
        parts.append(
            f"【风格示例】以下是上一章（第{style_sample_chapter}章）的开头段落，"
            f"请在文风、语气、节奏、描写密度上保持一致：\n{style_sample}"
        )
    # 大卷摘要先放（更早的、更压缩的剧情背景）
    if mega_summary_lines:
        parts.append("历史大卷摘要（每50章压缩）：\n" + "\n".join(mega_summary_lines))
    if arc_summary_lines:
        parts.append("近期卷摘要（每10章压缩）：\n" + "\n".join(arc_summary_lines))
    if summary_lines:
        parts.append("前文摘要（最近3章）：\n" + "\n".join(summary_lines))
    if prev_tail:
        parts.append(
            f"上一章（第{prev_number}章）正文结尾（截断 {prev_tail_len} 字）：\n{prev_tail}"
        )
    context_text = "\n\n".join(parts)
    manifest: dict[str, Any] = {
        "fact_count": len(facts),
        "fact_total": len(all_facts),
        "rag_fact_count": len(rag_facts),
        "fallback_fact_count": len(facts) - len(rag_facts),
        "rag_query": rag_query[:200],
        "summary_chapter_numbers": summary_chapter_numbers,
        "arc_summaries_injected": len(arc_summary_lines),
        "arc_summary_ranges": arc_summary_ranges,
        "mega_summaries_injected": len(mega_summary_lines),
        "mega_summary_ranges": mega_summary_ranges,
        "prev_chapter_number": prev_number,
        "prev_chapter_tail_chars": prev_tail_len,
        "few_shot_sample_chars": len(style_sample),
        "few_shot_sample_chapter": style_sample_chapter,
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


def _writer_num_predict(
    target_words: int,
    settings: Settings,
    writer_messages: list[dict[str, str]] | None = None,
    floor_from_prompt: int | None = None,
) -> int:
    """Calculate generation budget from available context window.

    Hard cap: prompt + num_predict must not exceed context_limit - reserve.
    When ctx is tight (large canon / long previous tail), num_predict shrinks
    below the configured floor rather than letting Ollama silently truncate the
    prompt — silent truncation drops the JSON tail, fails schema validation and
    triggers slow retry loops that double per-chapter latency.
    """
    floor = max(settings.writer_num_predict_floor, floor_from_prompt or 0)
    # Estimate prompt tokens (rough: 0.5 token/char for Chinese-heavy prompt)
    prompt_chars = sum(len(m.get("content", "")) for m in (writer_messages or []))
    prompt_tokens = int(prompt_chars * 0.5)
    # Reserve 512 tokens for safety margin; floor the budget at 512 so the model
    # still produces *some* output instead of an empty string when ctx is tiny.
    budget = max(512, settings.context_limit - prompt_tokens - 512)
    # Scaled target (1.6 tokens/char + JSON wrapper)
    scaled = int(target_words * 1.6) + 1024
    # Use floor only if it fits the remaining budget; otherwise shrink to budget
    # to avoid Ollama truncating the prompt and triggering schema repair retries.
    effective_floor = min(floor, budget)
    return max(effective_floor, min(budget, scaled))


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
    progress_callback: Callable[[str, str], None] | None = None,
) -> WriterLLMOutput:
    """Assess draft; expand or rewrite until quality_ok or repair budget exhausted."""
    def _emit(stage: str, msg: str) -> None:
        if progress_callback:
            progress_callback(stage, msg)

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
            _emit("repair", f"重写中（第 {repair_attempts}/{max_repair} 轮）：检测到大段重复...")
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
            _emit("repair", f"扩写中（第 {repair_attempts}/{max_repair} 轮）：字数不足 {len(writer_out.content)}/{min_words}...")
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
    record_gate(
        stage="writer_loop",
        repair_attempts=repair_attempts,
        quality_ok=quality.quality_ok,
        expanded=expanded_count > 0,
        rewritten=rewritten_count > 0,
        repetition_truncated=quality.repetition_truncated,
        repetition_severe=quality.repetition_severe,
        cut_ratio=round(quality.cut_ratio, 4),
        final_word_count=quality.word_count,
    )
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
                issue_lines.append("  → 修复方式：请根据硬设定和上下文自行修正")
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
            chapter.target_words,
            settings,
            polish_messages,
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
        _persist_failed_task(
            session, task.id, book.id, "chapter.polish", str(exc),
            chapter_id=chapter.id,
        )
        raise


def generate_chapter(
    session: Session,
    chapter_id: str,
    *,
    gateway: LLMGateway | None = None,
    settings: Settings | None = None,
    idempotency_key: str | None = None,
    progress_callback: Callable[[str, str], None] | None = None,
) -> GenerateChapterResult:
    settings = settings or get_settings()
    gateway = gateway or LLMGateway(settings=settings)

    def _emit(stage: str, msg: str) -> None:
        if progress_callback:
            progress_callback(stage, msg)

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

    _emit("context", "构建章节上下文（RAG检索 + 分层摘要 + few-shot）...")
    canon, context_manifest = _build_chapter_context(
        session, book, chapter, settings=settings
    )
    rag_count = context_manifest.get("rag_facts_count", 0)
    arc_count = context_manifest.get("arc_summaries_count", 0)
    _emit("context_done", f"上下文就绪：RAG事实 {rag_count} 条，卷摘要 {arc_count} 条")

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
            writer_messages,
            floor_from_prompt=writer_spec.default_params.get("num_predict"),
        )
        context_manifest["target_words"] = chapter.target_words
        context_manifest["min_words"] = min_words
        context_manifest["num_predict"] = num_predict

        _emit("generating", f"LLM 生成中（目标 {chapter.target_words} 字，预计 60-120 秒）...")
        writer_out, _ = gateway.chat_structured(
            writer_messages,
            WriterLLMOutput,
            temperature=writer_spec.default_params.get("temperature", 0.75),
            num_predict=num_predict,
        )
        _emit("generating_done", f"初稿完成：{len(writer_out.content)} 字")

        _emit("quality", "质量检查（字数/重复检测）...")
        writer_out = _apply_writer_quality_loop(
            gateway=gateway,
            writer_messages=writer_messages,
            writer_out=writer_out,
            chapter=chapter,
            min_words=min_words,
            num_predict=num_predict,
            settings=settings,
            context_manifest=context_manifest,
            progress_callback=progress_callback,
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

        change_outs: list[CandidateChangeOut] = []
        if settings.run_extractor:
            _emit("extractor", "抽取候选事实...")
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
        else:
            # 跳过 extractor 调用：不创建 change_set，confirm_chapter 检测不到时会直接跳过事实确认。
            change_set = None
            context_manifest["extractor_skipped"] = True

        chapter.current_version_id = version.id
        chapter.title = version.title
        chapter.status = "drafted"
        book.version += 1

        # ── consistency validation (optional) ──
        if settings.run_consistency_check:
            _emit("validation", "一致性校验...")
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
        else:
            val_issues, val_summary = [], ""
            context_manifest["validation_issue_count"] = 0

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
            change_set_id=change_set.id if change_set is not None else None,
            context_manifest=context_manifest,
            validation_issues=val_issues,
            validation_summary=val_summary,
        )
        task.status = TaskStatus.SUCCEEDED.value
        task.result_json = result.model_dump_json()
        session.flush()

        _emit("done", f"章节生成完成：{version.word_count} 字")

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
    except Exception as exc:
        _persist_failed_task(
            session, task.id, book.id, "chapter.generate", str(exc),
            chapter_id=chapter.id,
            idempotency_key=idempotency_key,
        )
        raise


def _arc_range_for_chapter(chapter_number: int, arc_size: int) -> tuple[int, int, int]:
    """Return (arc_index, chapter_start, chapter_end) for the arc containing chapter_number.

    arc_index is 1-based. arc 1 = chapters 1..arc_size, arc 2 = arc_size+1..2*arc_size.
    """
    arc_index = (chapter_number - 1) // arc_size + 1
    chapter_start = (arc_index - 1) * arc_size + 1
    chapter_end = arc_index * arc_size
    return arc_index, chapter_start, chapter_end


def generate_arc_summary(
    session: Session,
    book_id: str,
    *,
    arc_index: int,
    level: str = "arc",
    gateway: LLMGateway | None = None,
    settings: Settings | None = None,
) -> ArcSummaryRow:
    """Generate (or regenerate) an arc/mega-arc summary covering a chapter range.

    level="arc" uses arc_size (default 10 chapters).
    level="mega" uses mega_arc_size (default 50 chapters).

    Summaries are stored in arc_summaries table and indexed in FTS for retrieval.
    """
    settings = settings or get_settings()
    gateway = gateway or LLMGateway(settings=settings)
    book = _get_book(session, book_id)

    size = settings.arc_size if level == "arc" else settings.mega_arc_size
    chapter_start = (arc_index - 1) * size + 1
    chapter_end = arc_index * size

    # Fetch confirmed chapters in range
    chapters = list(
        session.scalars(
            select(ChapterRow)
            .where(
                ChapterRow.book_id == book_id,
                ChapterRow.number >= chapter_start,
                ChapterRow.number <= chapter_end,
                ChapterRow.status == "confirmed",
            )
            .order_by(ChapterRow.number)
        ).all()
    )
    if not chapters:
        raise PreconditionError(
            f"no confirmed chapters in range [{chapter_start}, {chapter_end}]",
            code="ARC_EMPTY",
        )

    chapter_payloads: list[dict[str, Any]] = []
    for ch in chapters:
        if not ch.current_version_id:
            continue
        ver = session.get(ChapterVersionRow, ch.current_version_id)
        if ver and (ver.summary or ver.content):
            chapter_payloads.append(
                {
                    "number": ch.number,
                    "title": ch.title or ver.title or "",
                    "summary": ver.summary or ver.content[:500],
                }
            )
    if not chapter_payloads:
        raise PreconditionError(
            f"no chapter versions with content in range [{chapter_start}, {chapter_end}]",
            code="ARC_EMPTY",
        )

    # Render prompt and call LLM
    registry = PromptRegistry(settings=settings)
    _spec, messages = registry.render(
        "writer/arc_summary.yaml",
        book_title=book.title,
        genre=book.genre,
        premise=book.premise,
        chapter_start=chapter_start,
        chapter_end=chapter_end,
        chapter_count=len(chapter_payloads),
        chapters=chapter_payloads,
    )
    result = gateway.chat_text(
        messages,
        num_predict=settings.arc_summary_num_predict,
        temperature=0.3,
    )
    summary_text = (result.content or "").strip()
    if not summary_text:
        raise PreconditionError(
            "arc summary LLM returned empty content",
            code="ARC_SUMMARY_EMPTY",
        )

    # Upsert into DB (replace existing if regeneration)
    existing = session.scalars(
        select(ArcSummaryRow).where(
            ArcSummaryRow.book_id == book_id,
            ArcSummaryRow.arc_index == arc_index,
            ArcSummaryRow.level == level,
        )
    ).first()
    if existing:
        existing.summary = summary_text
        existing.char_count = len(summary_text)
        existing.chapter_start = chapter_start
        existing.chapter_end = chapter_end
        existing.version += 1
        row = existing
    else:
        row = ArcSummaryRow(
            id=new_id(),
            book_id=book_id,
            arc_index=arc_index,
            chapter_start=chapter_start,
            chapter_end=chapter_end,
            level=level,
            summary=summary_text,
            char_count=len(summary_text),
        )
        session.add(row)
    session.flush()

    # Index in FTS for RAG retrieval (allows cross-arc lookup by keyword)
    upsert_fts(
        session,
        doc_id=f"arc_{level}:{row.id}",
        book_id=book_id,
        doc_type=f"arc_{level}_summary",
        title=f"第{arc_index}{'大卷' if level == 'mega' else '卷'}摘要 (ch {chapter_start}-{chapter_end})",
        body=summary_text,
    )
    return row


def maybe_generate_arc_summaries(
    session: Session,
    book_id: str,
    *,
    just_confirmed_chapter_number: int,
    gateway: LLMGateway | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Check if a new arc/mega-arc boundary was crossed; if so, generate summary.

    Called after confirm_chapter. Returns dict with generated summary ids.
    Skips silently if boundary not crossed or chapters incomplete.
    """
    settings = settings or get_settings()
    generated: dict[str, Any] = {"arc": None, "mega": None}

    # ── Arc boundary (every arc_size chapters) ────────────────────────────
    if just_confirmed_chapter_number % settings.arc_size == 0:
        arc_index = just_confirmed_chapter_number // settings.arc_size
        try:
            row = generate_arc_summary(
                session,
                book_id,
                arc_index=arc_index,
                level="arc",
                gateway=gateway,
                settings=settings,
            )
            generated["arc"] = {"arc_index": arc_index, "id": row.id, "chars": row.char_count}
        except PreconditionError:
            # Chapters not all confirmed yet; skip silently
            pass

    # ── Mega-arc boundary (every mega_arc_size chapters) ──────────────────
    if (
        settings.mega_arc_size > settings.arc_size
        and just_confirmed_chapter_number % settings.mega_arc_size == 0
    ):
        mega_index = just_confirmed_chapter_number // settings.mega_arc_size
        try:
            row = generate_arc_summary(
                session,
                book_id,
                arc_index=mega_index,
                level="mega",
                gateway=gateway,
                settings=settings,
            )
            generated["mega"] = {
                "mega_index": mega_index,
                "id": row.id,
                "chars": row.char_count,
            }
        except PreconditionError:
            pass

    return generated


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
            record_gate(
                stage="confirm_gate",
                repair_attempts=0,
                quality_ok=False,
                expanded=False,
                rewritten=False,
                repetition_truncated=quality.repetition_truncated,
                repetition_severe=quality.repetition_severe,
                cut_ratio=round(quality.cut_ratio, 4),
                final_word_count=quality.word_count,
            )
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

    # Trigger hierarchical summary generation if arc boundary crossed.
    # Best-effort: failures don't block confirm (chapter is already saved).
    arc_generated: dict[str, Any] = {"arc": None, "mega": None}
    try:
        arc_generated = maybe_generate_arc_summaries(
            session,
            chapter.book_id,
            just_confirmed_chapter_number=chapter.number,
            settings=settings,
        )
    except Exception:
        # Arc summary failure should not roll back the confirm transaction.
        # The summary can be regenerated later via generate_arc_summary.
        pass

    return {
        "chapter_id": chapter_id,
        "version_id": version.id,
        "status": "confirmed",
        "facts_added": facts_added,
        "arc_summaries": arc_generated,
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
    progress_callback: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    """One-click full pipeline: characters → outline → chapters → write all → complete → export.
    Returns dict with status updates and final export path."""
    settings = settings or get_settings()
    gateway = gateway or LLMGateway(settings=settings)
    log: list[str] = []

    def _report(msg: str) -> None:
        log.append(msg)

    def _emit(stage: str, msg: str) -> None:
        _report(msg)
        if progress_callback:
            progress_callback(stage, msg)

    try:
        _emit("characters", "📝 生成人物...")
        generate_characters(session, book_id, gateway=gateway, settings=settings, progress_callback=progress_callback)
        session.commit()
        _emit("characters_done", "✅ 人物生成完成")

        _emit("lock_chars", "🔒 锁定人物...")
        lock_characters(session, book_id)
        session.commit()
        _emit("lock_chars_done", "✅ 人物已锁定")

        _emit("outline", "📋 生成大纲...")
        generate_outline(session, book_id, gateway=gateway, settings=settings, progress_callback=progress_callback)
        session.commit()
        _emit("outline_done", "✅ 大纲生成完成")

        _emit("lock_outline", "🔒 锁定大纲...")
        lock_outline(session, book_id)
        session.commit()
        _emit("lock_outline_done", "✅ 大纲已锁定")

        _emit("plan", "📊 章节规划...")
        chapters = plan_chapters(session, book_id, settings=settings, progress_callback=progress_callback)
        session.commit()
        _emit("plan_done", f"✅ 共规划 {len(chapters)} 章")

        for i, ch in enumerate(chapters):
            _emit("chapter_start", f"✍️ 写作第 {i+1}/{len(chapters)} 章《{ch.title}》...")
            generate_chapter(session, ch.id, gateway=gateway, settings=settings, progress_callback=progress_callback)
            session.commit()
            confirm_chapter(session, ch.id, force=True, settings=settings)
            session.commit()
            _emit("chapter_done", f"✅ 第 {i+1}/{len(chapters)} 章完成")

        _emit("complete", "🏁 完本...")
        complete_book(session, book_id)
        session.commit()
        _emit("complete_done", "✅ 完本")

        _emit("export", "📥 导出...")
        export_result = export_book_txt(session, book_id, scope="all", settings=settings)
        session.commit()
        _emit("export_done", f"✅ 导出至 {export_result.path}")

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
        if progress_callback:
            progress_callback("error", f"❌ 失败：{exc}")
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
