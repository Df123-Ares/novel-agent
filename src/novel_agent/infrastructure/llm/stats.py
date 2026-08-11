"""Lightweight runtime metrics recording (no prompt/content payloads)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from novel_agent.settings import PROJECT_ROOT

_STATS_DIR = PROJECT_ROOT / "data" / "stats"
_LLM_CALLS = _STATS_DIR / "llm_calls.jsonl"
_GATES = _STATS_DIR / "quality_gates.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append(path: Path, record: dict[str, Any]) -> None:
    """Append one JSON line; metrics must never break the pipeline."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def record_llm_call(
    *,
    schema: str,
    attempts: int,
    success: bool,
    duration_ms: float,
    content_chars: int,
    prompt_eval_count: int | None,
    eval_count: int | None,
    total_duration_ms: float | None,
    rule_repaired: bool,
) -> None:
    _append(
        _LLM_CALLS,
        {
            "ts": _now(),
            "schema": schema,
            "attempts": attempts,
            "success": success,
            "rule_repaired": rule_repaired,
            "duration_ms": round(duration_ms, 1),
            "content_chars": content_chars,
            "prompt_eval_count": prompt_eval_count,
            "eval_count": eval_count,
            "total_duration_ms": round(total_duration_ms, 1) if total_duration_ms is not None else None,
        },
    )


def record_gate(
    *,
    stage: str,
    repair_attempts: int,
    quality_ok: bool,
    expanded: bool,
    rewritten: bool,
    repetition_truncated: bool,
    repetition_severe: bool,
    cut_ratio: float,
    final_word_count: int,
) -> None:
    _append(
        _GATES,
        {
            "ts": _now(),
            "stage": stage,
            "repair_attempts": repair_attempts,
            "quality_ok": quality_ok,
            "expanded": expanded,
            "rewritten": rewritten,
            "repetition_truncated": repetition_truncated,
            "repetition_severe": repetition_severe,
            "cut_ratio": round(cut_ratio, 4),
            "final_word_count": final_word_count,
        },
    )
