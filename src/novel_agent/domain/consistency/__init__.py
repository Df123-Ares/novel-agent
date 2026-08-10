"""Consistency / candidate fact schemas."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid4().hex


class ChangeStatus(str, Enum):
    PROPOSED = "PROPOSED"
    VALIDATED = "VALIDATED"
    CONFIRMED = "CONFIRMED"
    SUPERSEDED = "SUPERSEDED"
    REJECTED = "REJECTED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class ChangeKind(str, Enum):
    EVENT = "EVENT"
    CHARACTER_STATE = "CHARACTER_STATE"
    CHARACTER_KNOWLEDGE = "CHARACTER_KNOWLEDGE"
    RELATIONSHIP = "RELATIONSHIP"
    ITEM = "ITEM"
    LOCATION = "LOCATION"
    FORESHADOW = "FORESHADOW"
    OTHER = "OTHER"


class CandidateChange(BaseModel):
    id: str = Field(default_factory=_new_id)
    kind: ChangeKind
    subject: str = Field(description="Entity or character this change concerns")
    claim: str = Field(description="Factual claim extracted from the draft")
    evidence_quote: str | None = Field(
        default=None, description="Short quote from the draft supporting the claim"
    )
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    status: ChangeStatus = ChangeStatus.PROPOSED


class CandidateChangeSet(BaseModel):
    id: str = Field(default_factory=_new_id)
    chapter_id: str
    source_draft_id: str
    changes: list[CandidateChange] = Field(default_factory=list)
    status: ChangeStatus = ChangeStatus.PROPOSED
    created_at: datetime = Field(default_factory=_now)
    notes: str | None = None


class IssueCategory(str, Enum):
    CHARACTER_KNOWLEDGE = "CHARACTER_KNOWLEDGE"
    TIMELINE = "TIMELINE"
    CHARACTER_STATE = "CHARACTER_STATE"
    RELATIONSHIP = "RELATIONSHIP"
    WORLD_RULE = "WORLD_RULE"
    STYLE = "STYLE"
    OTHER = "OTHER"


class IssueSeverity(str, Enum):
    BLOCKER = "BLOCKER"
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class IssueStatus(str, Enum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class TextSpan(BaseModel):
    chapter_version_id: str
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)


class ReviewIssue(BaseModel):
    id: str = Field(default_factory=_new_id)
    category: IssueCategory
    severity: IssueSeverity
    status: IssueStatus = IssueStatus.OPEN
    text_span: TextSpan | None = None
    claim: str
    conflicting_fact: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    suggestion: str | None = None
    auto_fixable: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)
