#!/usr/bin/env python3
"""Run phase-0 write -> extract -> validate loop once."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from novel_agent.application.phase0_loop import run_phase0_loop  # noqa: E402
from novel_agent.domain.tasks import TaskStatus  # noqa: E402


def main() -> int:
    run = run_phase0_loop()
    summary = {
        "id": run.id,
        "status": run.status.value,
        "phase": run.phase.value,
        "model": run.model_name,
        "prompt_versions": run.prompt_versions,
        "draft_title": run.draft.title if run.draft else None,
        "draft_chars": len(run.draft.content) if run.draft else 0,
        "changes": len(run.change_set.changes) if run.change_set else 0,
        "error": run.error,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if run.draft:
        print("--- draft preview ---")
        print(run.draft.content[:400])
        print("---")
    return 0 if run.status == TaskStatus.SUCCEEDED else 1


if __name__ == "__main__":
    raise SystemExit(main())
