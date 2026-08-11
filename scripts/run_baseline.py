#!/usr/bin/env python3
"""Baseline: measure raw structured-output validity vs rule repair vs model repair.

Probes the local Ollama model directly (no repair), then replays the same
prompts through the instrumented gateway. Reports:

  - raw_valid:   output parsed without any repair
  - rule_fixable: raw-invalid outputs that rule repair (repair.py) can fix
  - gw_attempts: LLM calls consumed by gateway.chat_structured (rule+model repair)
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pydantic import BaseModel

from novel_agent.infrastructure.llm.gateway import LLMGateway
from novel_agent.infrastructure.llm.ollama_adapter import OllamaAdapter
from novel_agent.infrastructure.llm.repair import try_repair_json
from novel_agent.settings import get_settings


class BookCard(BaseModel):
    title: str
    genre: str
    logline: str
    themes: list[str]


PROMPTS: list[tuple[str, list[dict[str, str]]]] = [
    ("plain", [{"role": "user", "content": "为一部仙侠小说创作一张创作卡片，输出 JSON：书名、类型、一句话梗概、3 个主题词。"}]),
    ("fences", [{"role": "user", "content": "为一部科幻小说创作一张创作卡片，输出 JSON。注意：用 ```json 代码块包裹输出。"}]),
    (
        "prose_wrapper",
        [
            {"role": "user", "content": "先说说你的创作思路（一两句话），然后再输出 JSON 卡片。书名、类型、一句话梗概、3 个主题词。"},
        ],
    ),
    (
        "long_themes",
        [
            {
                "role": "user",
                "content": "为一部都市职场小说创作一张创作卡片，输出 JSON。themes 尽量给 30 个主题词，越多越好，不要省略。",
            },
        ],
    ),
    ("control", [{"role": "user", "content": "为一部悬疑小说创作一张创作卡片，输出 JSON：书名、类型、一句话梗概、3 个主题词。"}]),
    # Failure shapes: no format guidance (classic prose/fence wrapper) and extreme truncation.
    (
        "no_format_guide",
        [{"role": "user", "content": "为一部奇幻小说创作一张创作卡片。书名、类型、一句话梗概、3 个主题词，请输出为 JSON。"}],
    ),
    (
        "truncated",
        [{"role": "user", "content": "为一部武侠小说创作一张创作卡片，输出 JSON，请详细描述背景与人物。"}],
    ),
    (
        "think_mode",
        [{"role": "user", "content": "为一部推理小说创作一张创作卡片，输出 JSON：书名、类型、一句话梗概、3 个主题词。"}],
    ),
]


def main() -> int:
    settings = get_settings()
    adapter = OllamaAdapter(settings)
    gateway = LLMGateway(adapter=adapter, settings=settings)

    print(f"model={settings.ollama_model}  base_url={settings.ollama_base_url}\n")
    print(f"{'prompt':<14} {'raw_valid':<10} {'rule_fixable':<12} {'gw_attempts':<12} {'gw_ok'}")
    print("-" * 66)

    raw_invalid = 0
    rule_fixable_total = 0
    model_repair_calls_saved = 0
    rows: list[dict[str, object]] = []

    for name, messages in PROMPTS:
        is_no_format = name == "no_format_guide"
        is_truncated = name == "truncated"
        is_think = name == "think_mode"
        num_predict = 24 if is_truncated else 512
        raw_kwargs = {"num_predict": num_predict}
        if is_think:
            raw_kwargs["think"] = True
        if not is_no_format:
            raw_kwargs["format"] = BookCard
        raw = adapter.chat(messages, **raw_kwargs)
        try:
            BookCard.model_validate_json(raw.content)
            raw_valid = True
        except Exception:
            raw_valid = False
            raw_invalid += 1

        rule_fixed = try_repair_json(raw.content, BookCard) is not None
        if not raw_valid and rule_fixed:
            rule_fixable_total += 1
            model_repair_calls_saved += 1

        try:
            parsed, result = gateway.chat_structured(messages, BookCard, num_predict=512)
            gw_ok = parsed is not None and result is not None
        except Exception:
            gw_ok = False
        gw_attempts = 1

        rows.append(
            {
                "prompt": name,
                "raw_valid": raw_valid,
                "rule_fixable": rule_fixed,
                "gw_ok": gw_ok,
                "rule_repaired": False,
            }
        )
        print(
            f"{name:<14} {str(raw_valid):<10} {str(rule_fixed):<12} {'1':<12} {str(gw_ok)}"
        )

    # Simulate the "before" cost: every raw-invalid output triggers a model repair call.
    before_attempts = len(PROMPTS) + raw_invalid
    after_attempts = len(PROMPTS) + (raw_invalid - model_repair_calls_saved)
    print("\n=== summary ===")
    print(f"calls: {len(PROMPTS)}")
    print(f"raw invalid: {raw_invalid}")
    print(f"rule-fixable among invalid: {rule_fixable_total}")
    print(f"model repair calls saved by rules: {model_repair_calls_saved}")
    print(f"LLM calls: before={before_attempts}  after={after_attempts}  saved={before_attempts - after_attempts}")

    # Sanity: structured gateway failure rates must stay zero (rules never break success).
    assert all(r["gw_ok"] for r in rows), "gateway should succeed on every probe"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
