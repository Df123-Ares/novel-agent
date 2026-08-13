"""Pydantic DTOs for books / outline / characters / chapters."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CreateBookRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    genre: str = ""
    style: str = ""
    length: str = Field(default="medium", pattern="^(short|medium|long)$")
    perspective: str = "third"
    tone: str = ""
    premise: str = Field(min_length=1, description="一句话梗概")


class BookOut(BaseModel):
    id: str
    title: str
    genre: str
    style: str
    length: str
    perspective: str
    tone: str
    premise: str
    status: str
    version: int
    outline_locked: bool
    characters_locked: bool


class OutlineNodeOut(BaseModel):
    id: str
    parent_id: str | None
    level: str
    title: str
    summary: str
    sort_order: int
    locked: bool
    version: int


class UpdateOutlineNodeRequest(BaseModel):
    title: str | None = None
    summary: str | None = None
    expected_version: int


class CharacterOut(BaseModel):
    id: str
    name: str
    personality: str
    appearance: str
    background: str
    role: str
    locked: bool
    version: int


class UpdateCharacterRequest(BaseModel):
    name: str | None = None
    personality: str | None = None
    appearance: str | None = None
    background: str | None = None
    role: str | None = None
    expected_version: int


class ChapterOut(BaseModel):
    id: str
    number: int
    title: str
    goal: str
    target_words: int
    status: str
    outline_node_id: str | None
    current_version_id: str | None
    has_content: bool = False


class ChapterVersionOut(BaseModel):
    id: str
    chapter_id: str
    title: str
    content: str
    summary: str
    status: str
    word_count: int


class ChapterDetailOut(BaseModel):
    """Chapter metadata plus current version body (if any)."""

    chapter: ChapterOut
    current_version: ChapterVersionOut | None = None


class CandidateChangeOut(BaseModel):
    id: str
    kind: str
    subject: str
    claim: str
    evidence_quote: str | None
    confidence: float
    status: str


class ChapterValidationIssue(BaseModel):
    """A consistency issue found during chapter validation."""

    severity: str  # error | warning | info
    category: str  # OOC | timeline | foreshadowing | setting | logic | other
    subject: str
    description: str
    suggestion: str = ""


class GenerateChapterResult(BaseModel):
    task_id: str
    chapter: ChapterOut
    version: ChapterVersionOut
    changes: list[CandidateChangeOut]
    # None when RUN_EXTRACTOR=false (extractor skipped to save 1 LLM call per chapter)
    change_set_id: str | None = None
    context_manifest: dict = Field(default_factory=dict)
    validation_issues: list[ChapterValidationIssue] = Field(default_factory=list)
    validation_summary: str = ""


class WriteLoopItem(BaseModel):
    chapter_number: int
    chapter_id: str
    generate: GenerateChapterResult
    confirm: dict


class WriteLoopResult(BaseModel):
    book_id: str
    written: list[WriteLoopItem]
    confirmed_facts_total: int


class CompleteBookResult(BaseModel):
    book_id: str
    status: str
    chapter_count: int
    total_chars: int
    summary: str


class ExportBookResult(BaseModel):
    book_id: str
    format: str
    path: str
    chars: int
    scope: str = "confirmed"
    chapter_count: int = 0
    planned_chapter_count: int = 0


class TaskOut(BaseModel):
    id: str
    kind: str
    status: str
    book_id: str | None
    chapter_id: str | None
    error: str | None = None
