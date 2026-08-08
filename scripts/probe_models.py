#!/usr/bin/env python3
"""Probe local Ollama model capabilities and write a JSON report."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running without editable install
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from novel_agent.bootstrap import ensure_data_dirs  # noqa: E402
from novel_agent.infrastructure.llm.capabilities import probe_model  # noqa: E402
from novel_agent.infrastructure.llm.ollama_adapter import OllamaAdapter  # noqa: E402
from novel_agent.settings import get_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Ollama model capabilities")
    parser.add_argument(
        "--think",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Override OLLAMA_THINK (use --no-think for qwen3 structured output)",
    )
    parser.add_argument(
        "--expect-fail-without-think-off",
        action="store_true",
        help="Also probe with think=True and expect structured_output to fail (qwen3 sanity)",
    )
    args = parser.parse_args()

    settings = ensure_data_dirs(get_settings())
    adapter = OllamaAdapter(settings)

    report = probe_model(adapter, think=args.think)
    print(json.dumps(report.summarize(), ensure_ascii=False, indent=2))
    for check in report.checks:
        mark = "PASS" if check.passed else "FAIL"
        deg = " (degraded)" if check.degraded else ""
        print(f"[{mark}]{deg} {check.name}: {check.detail}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = settings.probe_report_dir / f"probe_{stamp}.json"
    out.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    print(f"report written: {out}")

    extra_ok = True
    if args.expect_fail_without_think_off:
        if "qwen3" not in adapter.model.lower() and "qwq" not in adapter.model.lower():
            print(
                "[SKIP] --expect-fail-without-think-off is qwen-specific; "
                f"not applicable to {adapter.model}"
            )
        else:
            bad = probe_model(adapter, think=True)
            structured = next(c for c in bad.checks if c.name == "structured_output")
            if structured.passed:
                print("[WARN] think=True unexpectedly passed structured_output")
                extra_ok = False
            else:
                print("[PASS] think=True correctly fails structured_output for qwen3")

    return 0 if report.ok and extra_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
