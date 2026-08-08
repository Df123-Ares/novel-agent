"""LLM output schemas for planner prompts."""

from __future__ import annotations

from pydantic import BaseModel, Field


class OutlineNodeLLM(BaseModel):
    level: str = Field(description="vision|arc|chapter_goal")
    title: str
    summary: str
    children: list[OutlineNodeLLM] = Field(default_factory=list)


class OutlineLLMOutput(BaseModel):
    root: OutlineNodeLLM


class CharacterLLM(BaseModel):
    name: str
    personality: str
    appearance: str
    background: str
    role: str = Field(description="protagonist|antagonist|supporting")


class CharactersLLMOutput(BaseModel):
    characters: list[CharacterLLM] = Field(default_factory=list)


# Rebuild for forward refs in OutlineNodeLLM
OutlineNodeLLM.model_rebuild()
