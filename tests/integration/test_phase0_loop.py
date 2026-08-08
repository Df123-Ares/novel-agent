"""Integration: phase-0 loop against local Ollama."""

from __future__ import annotations

import pytest

from novel_agent.application.phase0_loop import run_phase0_loop
from novel_agent.domain.consistency import ChangeStatus
from novel_agent.domain.tasks import TaskStatus
from novel_agent.infrastructure.llm.capabilities import probe_model
from novel_agent.infrastructure.llm.ollama_adapter import OllamaAdapter
from novel_agent.settings import get_settings


@pytest.fixture(scope="module")
def ollama_ready() -> OllamaAdapter:
    adapter = OllamaAdapter(get_settings())
    report = probe_model(adapter, think=False)
    if not report.ok:
        pytest.skip(f"Ollama probe failed: {report.summarize()}")
    return adapter


@pytest.mark.integration
def test_configured_think_mode_produces_structured_output(
    ollama_ready: OllamaAdapter,
) -> None:
    # The pipeline must produce valid structured output under the configured
    # OLLAMA_THINK flag, regardless of which model is in use.
    report = probe_model(ollama_ready, think=None)
    assert report.ok
    structured = next(c for c in report.checks if c.name == "structured_output")
    assert structured.passed


@pytest.mark.integration
def test_qwen3_requires_think_off(ollama_ready: OllamaAdapter) -> None:
    # qwen3 emits its answer into `thinking`, leaving `content` empty and
    # structured JSON invalid — unless think=False. Other reasoning models
    # (e.g. deepseek-r1) do not share this behavior, so gate it on the model.
    model = ollama_ready.model.lower()
    if "qwen3" not in model and "qwq" not in model:
        pytest.skip("qwen-specific think behavior does not apply to: " + ollama_ready.model)
    bad = probe_model(ollama_ready, think=True)
    structured = next(c for c in bad.checks if c.name == "structured_output")
    assert structured.passed is False


@pytest.mark.integration
def test_phase0_write_extract_validate(ollama_ready: OllamaAdapter, tmp_path) -> None:
    settings = get_settings()
    settings.generation_run_dir = tmp_path
    run = run_phase0_loop(settings=settings, save=True)
    assert run.status == TaskStatus.SUCCEEDED
    assert run.draft is not None
    assert len(run.draft.content) > 50
    assert run.change_set is not None
    assert run.change_set.status == ChangeStatus.PROPOSED
    assert run.prompt_versions
    saved = list(tmp_path.glob("*.json"))
    assert len(saved) == 1
