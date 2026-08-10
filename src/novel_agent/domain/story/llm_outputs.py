"""LLM-facing output schemas (subset of domain models)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from novel_agent.domain.consistency import ChangeKind


class WriterLLMOutput(BaseModel):
    title: str = Field(description="Chapter title")
    content: str = Field(description="Full chapter body in Chinese")
    scene_summary: str = Field(description="2-4 sentence summary of what happened")


class ExtractedChangeLLM(BaseModel):
    kind: ChangeKind
    subject: str
    claim: str
    evidence_quote: str | None = None
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)


class ExtractorLLMOutput(BaseModel):
    changes: list[ExtractedChangeLLM] = Field(default_factory=list)


class ChapterCheckIssueLLM(BaseModel):
    """A single consistency issue found during chapter validation."""

    severity: str = Field(description="error | warning | info")
    category: str = Field(description="OOC | timeline | foreshadowing | setting | logic | other")
    subject: str = Field(description="Character name or plot element affected")
    description: str = Field(description="Clear description of the issue")
    suggestion: str = Field(default="", description="Suggested fix")


class ChapterCheckLLMOutput(BaseModel):
    """LLM output for chapter consistency validation."""

    issues: list[ChapterCheckIssueLLM] = Field(default_factory=list)
    overall_assessment: str = Field(
        default="",
        description="One-sentence summary of the chapter's consistency quality",
    )
