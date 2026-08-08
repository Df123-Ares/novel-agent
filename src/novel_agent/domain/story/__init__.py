"""Story draft schemas for phase 0."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid4().hex


class ChapterDraft(BaseModel):
    id: str = Field(default_factory=_new_id)
    chapter_id: str
    title: str | None = None
    content: str = Field(min_length=1, description="Chapter body text")
    scene_summary: str | None = Field(
        default=None, description="Optional short summary of the scene"
    )
    word_count: int | None = None
    created_at: datetime = Field(default_factory=_now)

    @model_validator(mode="after")
    def _fill_word_count(self) -> ChapterDraft:
        if self.word_count is None:
            self.word_count = len(self.content)
        return self
