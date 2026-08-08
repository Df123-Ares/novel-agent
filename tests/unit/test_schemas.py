"""Unit tests for frozen schemas and task transitions (no LLM)."""

from __future__ import annotations

import pytest

from novel_agent.domain.consistency import (
    CandidateChange,
    CandidateChangeSet,
    ChangeKind,
    ChangeStatus,
    IssueCategory,
    IssueSeverity,
    ReviewIssue,
    TextSpan,
)
from novel_agent.domain.story import ChapterDraft
from novel_agent.domain.story.llm_outputs import ExtractorLLMOutput, WriterLLMOutput
from novel_agent.domain.tasks import TaskStatus, can_transition
from novel_agent.domain.tasks.generation_run import GenerationRun


def test_task_transitions_phase0_path() -> None:
    assert can_transition(TaskStatus.QUEUED, TaskStatus.RUNNING)
    assert can_transition(TaskStatus.RUNNING, TaskStatus.SUCCEEDED)
    assert can_transition(TaskStatus.RUNNING, TaskStatus.FAILED)
    assert can_transition(TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL)
    assert not can_transition(TaskStatus.SUCCEEDED, TaskStatus.RUNNING)


def test_chapter_draft_word_count() -> None:
    draft = ChapterDraft(chapter_id="ch-1", content="你好世界")
    assert draft.word_count == 4


def test_candidate_change_set_roundtrip() -> None:
    cs = CandidateChangeSet(
        chapter_id="ch-1",
        source_draft_id="d1",
        changes=[
            CandidateChange(
                kind=ChangeKind.EVENT,
                subject="林澄",
                claim="发现撕页日志",
                status=ChangeStatus.PROPOSED,
            )
        ],
    )
    restored = CandidateChangeSet.model_validate_json(cs.model_dump_json())
    assert restored.changes[0].subject == "林澄"


def test_review_issue_appendix_b_shape() -> None:
    issue = ReviewIssue(
        category=IssueCategory.CHARACTER_KNOWLEDGE,
        severity=IssueSeverity.ERROR,
        text_span=TextSpan(chapter_version_id="v1", start_offset=10, end_offset=20),
        claim="角色知道密室钥匙位置",
        conflicting_fact="角色尚未获得该信息",
        evidence_ids=["e1"],
        confidence=0.9,
        suggestion="改为猜测",
        auto_fixable=False,
    )
    data = issue.model_dump()
    assert data["category"] == "CHARACTER_KNOWLEDGE"
    assert data["severity"] == "ERROR"


def test_writer_extractor_llm_schemas() -> None:
    writer = WriterLLMOutput(title="夜班", content="正文", scene_summary="摘要")
    extractor = ExtractorLLMOutput.model_validate(
        {
            "changes": [
                {
                    "kind": "EVENT",
                    "subject": "林澄",
                    "claim": "发现日志",
                    "confidence": 0.8,
                }
            ]
        }
    )
    assert writer.title == "夜班"
    assert extractor.changes[0].kind == ChangeKind.EVENT


def test_generation_run_transition_and_save(tmp_path) -> None:
    run = GenerationRun(chapter_id="ch-1", model_name="qwen3:8b")
    run.transition(TaskStatus.RUNNING)
    run.transition(TaskStatus.SUCCEEDED)
    path = run.save(tmp_path)
    loaded = GenerationRun.load(path)
    assert loaded.status == TaskStatus.SUCCEEDED

    with pytest.raises(ValueError):
        loaded.transition(TaskStatus.RUNNING)
