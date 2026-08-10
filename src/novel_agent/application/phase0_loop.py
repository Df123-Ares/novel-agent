"""Phase-0 closed loop: write draft -> extract changes -> validate schemas."""

from __future__ import annotations

from novel_agent.bootstrap import ensure_data_dirs
from novel_agent.domain.consistency import (
    CandidateChange,
    CandidateChangeSet,
    ChangeStatus,
)
from novel_agent.domain.story import ChapterDraft
from novel_agent.domain.story.llm_outputs import ExtractorLLMOutput, WriterLLMOutput
from novel_agent.domain.tasks import TaskStatus
from novel_agent.domain.tasks.generation_run import GenerationPhase, GenerationRun
from novel_agent.infrastructure.llm.gateway import LLMGateway
from novel_agent.infrastructure.prompts import PromptRegistry
from novel_agent.settings import Settings, get_settings

DEFAULT_CANON = """
书名：雾港回声
题材：都市悬疑
视角：第三人称
硬设定：
- 主角林澄是港口档案馆夜班管理员，不相信超自然。
- Foghorn 灯塔已废弃三年，夜间仍偶有灯光。
- 林澄丢失的妹妹林晚在失踪前最后出现在灯塔附近。
""".strip()

DEFAULT_CHAPTER_GOAL = (
    "林澄在夜班清点旧航海日志时，发现一页被撕走后又夹回的记录，"
    "上面写着与妹妹失踪当晚相同的日期，并提到灯塔灯光。他决定天亮前去看一眼。"
)

DEFAULT_CHAPTER_TITLE = "档案馆的夜班"
DEFAULT_TARGET_WORDS = 2000


def run_phase0_loop(
    *,
    chapter_id: str = "ch-001",
    canon: str = DEFAULT_CANON,
    chapter_goal: str = DEFAULT_CHAPTER_GOAL,
    chapter_title: str = DEFAULT_CHAPTER_TITLE,
    target_words: int = DEFAULT_TARGET_WORDS,
    settings: Settings | None = None,
    gateway: LLMGateway | None = None,
    save: bool = True,
) -> GenerationRun:
    settings = ensure_data_dirs(settings or get_settings())
    gateway = gateway or LLMGateway(settings=settings)
    registry = PromptRegistry(settings=settings)

    run = GenerationRun(
        chapter_id=chapter_id,
        model_name=settings.ollama_model,
        think=settings.ollama_think,
        context_manifest={
            "canon_chars": len(canon),
            "chapter_goal": chapter_goal,
        },
    )
    run.transition(TaskStatus.RUNNING)

    try:
        run.phase = GenerationPhase.WRITE
        writer_spec, writer_messages = registry.render(
            "writer/chapter_draft.yaml",
            canon=canon,
            chapter_id=chapter_id,
            chapter_goal=chapter_goal,
            chapter_title=chapter_title,
            target_words=target_words,
            min_words=max(400, int(target_words * settings.writer_min_words_ratio)),
        )
        run.prompt_versions[writer_spec.prompt_id] = writer_spec.version
        writer_out, _ = gateway.chat_structured(
            writer_messages,
            WriterLLMOutput,
            temperature=writer_spec.default_params.get("temperature", 0.7),
            num_predict=writer_spec.default_params.get("num_predict"),
        )
        draft = ChapterDraft(
            chapter_id=chapter_id,
            title=writer_out.title,
            content=writer_out.content,
            scene_summary=writer_out.scene_summary,
        )
        run.draft = draft

        run.phase = GenerationPhase.EXTRACT
        extractor_spec, extractor_messages = registry.render(
            "extractor/candidate_changes.yaml",
            chapter_id=chapter_id,
            canon=canon,
            content=draft.content,
        )
        run.prompt_versions[extractor_spec.prompt_id] = extractor_spec.version
        extractor_out, _ = gateway.chat_structured(
            extractor_messages,
            ExtractorLLMOutput,
            temperature=extractor_spec.default_params.get("temperature", 0.2),
            num_predict=extractor_spec.default_params.get("num_predict"),
        )

        run.phase = GenerationPhase.VALIDATE
        changes = [
            CandidateChange(
                kind=item.kind,
                subject=item.subject,
                claim=item.claim,
                evidence_quote=item.evidence_quote,
                confidence=item.confidence,
                status=ChangeStatus.PROPOSED,
            )
            for item in extractor_out.changes
        ]
        change_set = CandidateChangeSet(
            chapter_id=chapter_id,
            source_draft_id=draft.id,
            changes=changes,
            status=ChangeStatus.PROPOSED,
        )
        run.change_set = change_set
        run.phase = GenerationPhase.DONE
        run.transition(TaskStatus.SUCCEEDED)
    except Exception as exc:
        run.error = str(exc)
        run.mark_rejected_changes(str(exc))
        if run.status == TaskStatus.RUNNING:
            run.transition(TaskStatus.FAILED)
        if save:
            run.save(settings.generation_run_dir)
        raise

    if save:
        run.save(settings.generation_run_dir)
    return run
