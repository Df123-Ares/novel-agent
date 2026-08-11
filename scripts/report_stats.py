#!/usr/bin/env python3
"""Aggregate data/stats/*.jsonl into a P0 acceptance report.

Distinguishes production calls from baseline probes by source file:
- llm_calls.jsonl     -> production + probe pollution (callers using the gateway)
- calibration.jsonl   -> B1 calibration probes (separate file, not via record_llm_call)
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATS_DIR = ROOT / "data" / "stats"


def load_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def as_int(value: object) -> int | None:
    return int(value) if isinstance(value, (int, float)) and value is not None else None


def report_llm(rows: list[dict[str, object]], title: str) -> None:
    if not rows:
        print(f"== {title}: 无数据")
        return
    print(f"== {title} ({len(rows)} records)")
    by_schema: dict[str, list[dict[str, object]]] = defaultdict(list)
    for r in rows:
        by_schema[str(r.get("schema", "?") )].append(r)

    for schema, items in sorted(by_schema.items()):
        total = len(items)
        ok = sum(1 for r in items if r.get("success"))
        multi = sum(1 for r in items if (as_int(r.get("attempts")) or 0) > 1)
        ruled = sum(1 for r in items if r.get("rule_repaired"))
        cap_hits = sum(
            1
            for r in items
            for d in (r.get("attempts_detail") or [])
            if isinstance(d, dict) and d.get("cap_hit")
        )
        durations = [as_int(r.get("duration_ms")) for r in items]
        durations = [d for d in durations if d is not None]
        avg_ms = sum(durations) / len(durations) if durations else 0
        print(
            f"  {schema:<22} n={total:<3} success={ok:<3} multi-attempt={multi:<2} "
            f"rule_repaired={ruled:<2} cap_hit={cap_hits:<2} avg_dur={avg_ms / 1000:.0f}s"
        )
    errors = Counter(str(r.get("schema")) + ":" + str(d.get("error")) for r in rows for d in (r.get("attempts_detail") or []) if isinstance(d, dict) and d.get("error"))
    if errors:
        print("  error classes:", dict(errors))


def report_gates(rows: list[dict[str, object]]) -> None:
    if not rows:
        print("== quality_gates: 无数据")
        return
    print(f"== quality_gates ({len(rows)} records)")
    by_stage: dict[str, list[dict[str, object]]] = defaultdict(list)
    for r in rows:
        by_stage[str(r.get("stage", "?"))].append(r)
    for stage, items in sorted(by_stage.items()):
        total = len(items)
        ok = sum(1 for r in items if r.get("quality_ok"))
        expanded = sum(1 for r in items if r.get("expanded"))
        rewritten = sum(1 for r in items if r.get("rewritten"))
        repairs = [as_int(r.get("repair_attempts")) for r in items]
        repairs = [x for x in repairs if x is not None]
        avg_repair = sum(repairs) / len(repairs) if repairs else 0
        words = [as_int(r.get("final_word_count")) for r in items]
        words = [w for w in words if w is not None]
        avg_words = sum(words) / len(words) if words else 0
        print(
            f"  {stage:<14} n={total:<3} ok={ok:<3} expanded={expanded:<2} rewritten={rewritten:<2} "
            f"avg_repair={avg_repair:.2f} avg_words={avg_words:.0f}"
        )


def main() -> int:
    calls = load_jsonl(STATS_DIR / "llm_calls.jsonl")
    calibration = load_jsonl(STATS_DIR / "calibration.jsonl")
    gates = load_jsonl(STATS_DIR / "quality_gates.jsonl")
    print(f"stats dir: {STATS_DIR}\n")
    report_llm(calls, "llm_calls (production+gateway)")
    print()
    report_llm(calibration, "calibration (B1 probes)")
    print()
    report_gates(gates)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
