#!/usr/bin/env python3
"""B1 calibration: measure real chars/token ratio for writer-like outputs.

Writes to data/stats/calibration.jsonl (separate from production stats).
Validates the book_flow coefficient (currently 1.6 tokens/char) and reports
whether the writer hits its num_predict cap (truncation -> too_short -> expand).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pydantic import BaseModel

from novel_agent.infrastructure.llm.ollama_adapter import OllamaAdapter
from novel_agent.settings import Settings, get_settings

OUT_FILE = ROOT / "data" / "stats" / "calibration.jsonl"

# book_flow estimator: num_predict = max(floor, min(ceil, target_words * COEFF + 1024))
COEFF = 1.6
FLOOR = 4096
CEIL = 12288


class WriterOut(BaseModel):
    title: str
    content: str
    scene_summary: str


TARGETS = [1000, 2000, 4000]


def build_messages(target: int) -> list[dict[str, str]]:
    return [
        {
            "role": "user",
            "content": (
                "你是武侠小说作者。请写一章小说章节，正文目标约 "
                f"{target} 字：\n"
                "- 主角沈孤鸿在剑冢得到一柄锈剑，当晚山雨欲来\n"
                "- 包含对话、动作、环境描写，情节完整推进\n"
                "输出 JSON：title（章节名）、content（正文）、scene_summary（场景摘要）。"
            ),
        }
    ]


def main() -> int:
    settings: Settings = get_settings()
    adapter = OllamaAdapter(settings)
    out_file = OUT_FILE
    out_file.parent.mkdir(parents=True, exist_ok=True)

    print(f"model={settings.ollama_model}\n")
    print(f"{'target':>6} {'num_predict':>11} {'chars':>7} {'eval':>6} {'ratio':>6} {'cap_hit':>8}")
    print("-" * 54)

    rows: list[dict[str, object]] = []
    for target in TARGETS:
        for run in range(2):
            num_predict = max(FLOOR, min(CEIL, int(target * COEFF) + 1024))
            messages = build_messages(target)
            result = adapter.chat(
                messages,
                format=WriterOut,
                num_predict=num_predict,
                temperature=0.7,
            )
            raw = result.raw or {}
            eval_count = raw.get("eval_count")
            eval_count = int(eval_count) if isinstance(eval_count, (int, float)) else None
            content_chars = len(result.content)
            ratio = round(content_chars / eval_count, 2) if eval_count else None
            cap_hit = eval_count is not None and eval_count >= num_predict
            rows.append(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "target_words": target,
                    "run": run,
                    "num_predict": num_predict,
                    "content_chars": content_chars,
                    "eval_count": eval_count,
                    "ratio_chars_per_token": ratio,
                    "cap_hit": cap_hit,
                    "valid_json": True,
                }
            )
            print(
                f"{target:>6} {num_predict:>11} {content_chars:>7} "
                f"{eval_count!s:>6} {str(ratio):>6} {str(cap_hit):>8}"
            )

    with open(out_file, "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    ratios = [float(r["ratio_chars_per_token"]) for r in rows if r["ratio_chars_per_token"]]
    cap_hits = sum(1 for r in rows if r["cap_hit"])
    if ratios:
        median = sorted(ratios)[len(ratios) // 2]
        # coeff_safe = tokens per char with 25% safety margin
        tokens_per_char = 1.0 / median
        suggested = round(tokens_per_char * 1.25, 2)
        print("\n=== summary ===")
        print(f"median chars/token: {median}  (tokens/char: {tokens_per_char:.3f})")
        print(f"cap_hit count: {cap_hits} / {len(rows)}")
        print(f"current coeff: {COEFF}  suggested: ~{suggested} (safety margin 25%)")
        print(f"logged: {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
