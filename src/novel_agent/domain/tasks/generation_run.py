"""Generation run audit record (phase 0: JSON file persistence)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from novel_agent.domain.consistency import CandidateChangeSet, ChangeStatus, ReviewIssue
from novel_agent.domain.story import ChapterDraft
from novel_agent.domain.tasks import TaskStatus, can_transition


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid4().hex


class GenerationPhase(str, Enum):
    WRITE = "WRITE"
    EXTRACT = "EXTRACT"
    VALIDATE = "VALIDATE"
    DONE = "DONE"


class GenerationRun(BaseModel):
    id: str = Field(default_factory=_new_id)
    chapter_id: str
    status: TaskStatus = TaskStatus.QUEUED
    phase: GenerationPhase = GenerationPhase.WRITE
    model_name: str
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    think: bool = False
    draft: ChapterDraft | None = None
    change_set: CandidateChangeSet | None = None
    issues: list[ReviewIssue] = Field(default_factory=list)
    error: str | None = None
    context_manifest: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    def transition(self, target: TaskStatus) -> None:
        if not can_transition(self.status, target):
            raise ValueError(f"Invalid transition {self.status} -> {target}")
        self.status = target
        self.updated_at = _now()

    def mark_rejected_changes(self, reason: str) -> None:
        if self.change_set is not None:
            self.change_set.status = ChangeStatus.REJECTED
            self.change_set.notes = reason

    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.id}.json"
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> GenerationRun:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))
