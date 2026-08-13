"""ORM models for phase-1 MVP."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from novel_agent.infrastructure.persistence.models.base import (
    Base,
    IdMixin,
    TimestampMixin,
    utcnow,
)


class BookRow(Base, IdMixin, TimestampMixin):
    __tablename__ = "books"

    title: Mapped[str] = mapped_column(String(200))
    genre: Mapped[str] = mapped_column(String(64), default="")
    style: Mapped[str] = mapped_column(String(64), default="")
    length: Mapped[str] = mapped_column(String(32), default="medium")  # short|medium|long
    perspective: Mapped[str] = mapped_column(String(32), default="third")
    tone: Mapped[str] = mapped_column(String(64), default="")
    premise: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)
    outline_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    characters_locked: Mapped[bool] = mapped_column(Boolean, default=False)

    outline_nodes: Mapped[list[OutlineNodeRow]] = relationship(back_populates="book")
    characters: Mapped[list[CharacterRow]] = relationship(back_populates="book")
    chapters: Mapped[list[ChapterRow]] = relationship(back_populates="book")


class OutlineNodeRow(Base, IdMixin, TimestampMixin):
    __tablename__ = "outline_nodes"

    book_id: Mapped[str] = mapped_column(ForeignKey("books.id"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("outline_nodes.id"), nullable=True)
    level: Mapped[str] = mapped_column(String(32))  # vision|arc|chapter_goal|scene
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)

    book: Mapped[BookRow] = relationship(back_populates="outline_nodes")


class CharacterRow(Base, IdMixin, TimestampMixin):
    __tablename__ = "characters"

    book_id: Mapped[str] = mapped_column(ForeignKey("books.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    personality: Mapped[str] = mapped_column(Text, default="")
    appearance: Mapped[str] = mapped_column(Text, default="")
    background: Mapped[str] = mapped_column(Text, default="")
    role: Mapped[str] = mapped_column(String(64), default="supporting")
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)

    book: Mapped[BookRow] = relationship(back_populates="characters")


class ChapterRow(Base, IdMixin, TimestampMixin):
    __tablename__ = "chapters"

    book_id: Mapped[str] = mapped_column(ForeignKey("books.id"), index=True)
    outline_node_id: Mapped[str | None] = mapped_column(
        ForeignKey("outline_nodes.id"), nullable=True
    )
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200), default="")
    goal: Mapped[str] = mapped_column(Text, default="")
    target_words: Mapped[int] = mapped_column(Integer, default=2000)
    status: Mapped[str] = mapped_column(String(32), default="planned")  # planned|drafted|confirmed
    current_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    book: Mapped[BookRow] = relationship(back_populates="chapters")
    versions: Mapped[list[ChapterVersionRow]] = relationship(
        back_populates="chapter", cascade="all, delete-orphan"
    )


class ChapterVersionRow(Base, IdMixin, TimestampMixin):
    __tablename__ = "chapter_versions"

    chapter_id: Mapped[str] = mapped_column(ForeignKey("chapters.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="DRAFT")  # DRAFT|CONFIRMED
    word_count: Mapped[int] = mapped_column(Integer, default=0)

    chapter: Mapped[ChapterRow] = relationship(back_populates="versions")


class CandidateChangeSetRow(Base, IdMixin, TimestampMixin):
    __tablename__ = "candidate_change_sets"

    book_id: Mapped[str] = mapped_column(ForeignKey("books.id"), index=True)
    chapter_id: Mapped[str] = mapped_column(ForeignKey("chapters.id"), index=True)
    chapter_version_id: Mapped[str] = mapped_column(ForeignKey("chapter_versions.id"))
    status: Mapped[str] = mapped_column(String(32), default="PROPOSED")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    changes: Mapped[list[CandidateChangeRow]] = relationship(
        back_populates="change_set", cascade="all, delete-orphan"
    )


class CandidateChangeRow(Base, IdMixin, TimestampMixin):
    __tablename__ = "candidate_changes"

    change_set_id: Mapped[str] = mapped_column(ForeignKey("candidate_change_sets.id"), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(200))
    claim: Mapped[str] = mapped_column(Text)
    evidence_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    status: Mapped[str] = mapped_column(String(32), default="PROPOSED")

    change_set: Mapped[CandidateChangeSetRow] = relationship(back_populates="changes")


class ConfirmedFactRow(Base, IdMixin, TimestampMixin):
    __tablename__ = "confirmed_facts"

    book_id: Mapped[str] = mapped_column(ForeignKey("books.id"), index=True)
    chapter_id: Mapped[str] = mapped_column(ForeignKey("chapters.id"), index=True)
    source_change_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    kind: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(200))
    claim: Mapped[str] = mapped_column(Text)
    evidence_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="CONFIRMED")


class ArcSummaryRow(Base, IdMixin, TimestampMixin):
    """Hierarchical summary for long-novel context compression.

    Every `arc_size` confirmed chapters (default 10) triggers an arc summary
    covering that volume. Injected into `_build_chapter_context` so chapter N
    can see plot developments from chapters N-50, N-30, N-20 — not just the
    last 3 chapter summaries.
    """

    __tablename__ = "arc_summaries"

    book_id: Mapped[str] = mapped_column(ForeignKey("books.id"), index=True)
    # 1-based volume index: arc 1 covers chapters 1..arc_size, arc 2 covers
    # arc_size+1..2*arc_size, etc.
    arc_index: Mapped[int] = mapped_column(Integer, index=True)
    # inclusive chapter range this summary covers
    chapter_start: Mapped[int] = mapped_column(Integer)
    chapter_end: Mapped[int] = mapped_column(Integer)
    # "arc" (10-chapter) or "mega" (50-chapter). mega covers 5 arcs.
    level: Mapped[str] = mapped_column(String(16), default="arc")
    summary: Mapped[str] = mapped_column(Text)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)

    __table_args__ = (
        UniqueConstraint("book_id", "arc_index", "level", name="uq_arc_summaries_per_book_level"),
    )


class TaskRow(Base, IdMixin, TimestampMixin):
    __tablename__ = "tasks"

    book_id: Mapped[str | None] = mapped_column(ForeignKey("books.id"), nullable=True, index=True)
    chapter_id: Mapped[str | None] = mapped_column(ForeignKey("chapters.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="QUEUED")
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_tasks_idempotency"),)


class IdempotencyRow(Base, TimestampMixin):
    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    route: Mapped[str] = mapped_column(String(200))
    response_json: Mapped[str] = mapped_column(Text)
    status_code: Mapped[int] = mapped_column(Integer, default=200)
