#!/usr/bin/env python3
"""Probe: measure num_ctx 4096 vs 6144 vs 8192 on REAL writer prompts.

Uses the real chapter_draft prompt rendered from the actual DB book state
(facts / characters / previous summaries / prev-tail), so offload ratio,
speed and truncation results transfer to production.

Per ctx: two calls — the first reloads the model (load_duration included),
the second measures steady-state generation speed.

Truncation detection: ollama's prompt_eval_count vs the tokenized prompt
length. If prompt_eval < sent_tokens, ollama cut the prompt head — the
failure mode this probe is designed to quantify.

Logs one JSON row per (ctx, phase) to data/stats/probe_num_ctx.jsonl.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import httpx
import ollama
from pydantic import BaseModel
from sqlalchemy import select

from novel_agent.application.workflows.book_flow import (
    _build_chapter_context,
    _min_words_for_chapter,
)
from novel_agent.infrastructure.persistence.db import get_session_factory
from novel_agent.infrastructure.persistence.models import BookRow, ChapterRow
from novel_agent.infrastructure.prompts import PromptRegistry
from novel_agent.settings import get_settings

OUT_FILE = ROOT / "data" / "stats" / "probe_num_ctx.jsonl"
CTX_VALUES = (4096, 6144, 8192)
NUM_PREDICT = 384


class WriterOut(BaseModel):
    title: str
    content: str
    scene_summary: str


def _message_text(messages: list[dict[str, str]]) -> str:
    return "\n".join(m.get("content", "") for m in messages)


def _tokenize(client: ollama.Client, model: str, text: str) -> int:
    base = get_settings().ollama_base_url
    try:
        resp = httpx.post(f"{base}/api/tokenize", json={"model": model, "prompt": text}, timeout=60)
        if resp.status_code == 200:
            data = resp.json()
            return int(data.get("count") or 0)
    except Exception:
        pass
    return int(len(text) * 0.3)


def _build_real_messages():
    """Render the real chapter_draft prompt from DB state; None if no data."""
    settings = get_settings()
    factory = get_session_factory(settings)
    with factory() as session:
        book = session.scalars(select(BookRow).order_by(BookRow.created_at)).first()
        if book is None:
            return None
        chapters = list(
            session.scalars(
                select(ChapterRow)
                .where(ChapterRow.book_id == book.id)
                .order_by(ChapterRow.number)
            ).all()
        )
        chapter = next((c for c in chapters if c.status != "confirmed"), None)
        if chapter is None:
            return None
        canon, _ = _build_chapter_context(session, book, chapter, settings=settings)
        min_words = _min_words_for_chapter(chapter.target_words, settings)
        registry = PromptRegistry(settings=settings)
        _, messages = registry.render(
            "writer/chapter_draft.yaml",
            canon=canon,
            chapter_id=chapter.id,
            chapter_title=chapter.title,
            target_words=chapter.target_words,
            min_words=min_words,
            chapter_goal=chapter.goal,
        )
        print(f"[probe] real prompt from book={book.title!r} chapter #{chapter.number}")
        return messages


def _synthetic_messages(big: bool = False):
    """Fallback: writer-like prompt with a large canon (~4K tokens when big)."""
    n_facts = 200 if big else 50
    n_tail = 230 if big else 60
    n_chars = 16 if big else 12
    canon = "\n".join(
        [
            "书名：《剑冢孤鸿》\n类型：武侠\n风格：古风\n视角：第三人称\n基调：苍凉\n梗概：主角沈孤鸿寻剑复仇。\n人物：",
            *[
                f"- 人物{i}：性格=冷峻隐忍；外貌=青衫负剑；背景=江南剑门遗孤"
                for i in range(n_chars)
            ],
            "已确认事实：",
            *[
                "- [EVENT] 沈孤鸿在剑冢拾得锈剑「蝉鸣」，剑身有裂纹一条"
                for _ in range(n_facts)
            ],
            *(
                [
                    "前文摘要：\n"
                    "第1章《山雨欲来》摘要：沈孤鸿夜入剑冢，得剑，山中狼群环伺。\n"
                    "第2章《锈剑鸣》摘要：剑冢山门夜战，沈孤鸿以蝉鸣断狼王脊骨。\n"
                    "第3章《夜奔》摘要：沈孤鸿携剑下山，遇江南旧识柳轻眉。"
                ]
                * (7 if big else 1)
            ),
            "上一章（第3章）正文结尾（截断 1500 字）：\n"
            + ("雨势渐急，柳轻眉压低斗笠，低声道：「沈郎，你背后那柄剑……在抖。」" * n_tail),
        ]
    )
    settings = get_settings()
    registry = PromptRegistry(settings=settings)
    _, messages = registry.render(
        "writer/chapter_draft.yaml",
        canon=canon,
        chapter_id="probe-synthetic",
        chapter_title="第四章 蝉鸣",
        target_words=1500,
        min_words=825,
        chapter_goal="沈孤鸿与柳轻眉夜宿荒村，遇昔日仇家探子",
    )
    print(f"[probe] fallback synthetic prompt (big={big})")
    return messages


def _gpu_layers_offloaded() -> int | None:
    """Parse the newest 'offloaded N/37 layers' line from ollama server logs."""
    import re

    log_dir = Path.home() / "AppData" / "Local" / "ollama"
    found: int | None = None
    for log in sorted(log_dir.glob("server*.log"), key=lambda p: p.stat().st_mtime, reverse=True):
        for line in log.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.search(r"offloaded (\d+)/(\d+) layers", line)
            if m:
                found = int(m.group(1))
        if found is not None:
            break
    return found


def gpu_snapshot(label: str) -> None:
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu", "--format=csv,noheader"],
        capture_output=True,
        text=True,
    )
    ps = subprocess.run(["ollama", "ps"], capture_output=True, text=True)
    print(f"  [{label}] nvidia-smi: {out.stdout.strip()}")
    for line in ps.stdout.strip().splitlines()[1:]:
        print(f"  [{label}] ollama ps: {line}")


def _measure(
    client: ollama.Client,
    messages: list[dict[str, str]],
    ctx: int,
    sent: int,
    num_predict: int,
    phase: str,
) -> dict[str, object]:
    start = time.perf_counter()
    resp = client.chat(
        model=settings.ollama_model,
        messages=messages,
        format=WriterOut.model_json_schema(),
        options={"num_ctx": ctx, "num_predict": num_predict, "temperature": 0.7},
    )
    wall = time.perf_counter() - start
    data = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
    msg = data.get("message") or {}
    content = (msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")) or ""
    load_ms = data.get("load_duration", 0) / 1e6
    ppre = data.get("prompt_eval_duration", 0) / 1e6
    eval_ms = data.get("eval_duration", 0) / 1e6
    n_eval = data.get("eval_count", 0) or 0
    p_eval = data.get("prompt_eval_count", 0) or 0
    tok_s = n_eval / (eval_ms / 1000) if eval_ms else 0
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "ctx": ctx,
        "phase": phase,
        "sent_tokens": sent,
        "prompt_eval_count": p_eval,
        "truncated": p_eval < sent,
        "eval_count": n_eval,
        "tok_s": round(tok_s, 1),
        "wall_s": round(wall, 1),
        "load_ms": round(load_ms),
        "prefill_ms": round(ppre),
        "eval_ms": round(eval_ms),
        "layers_offloaded": _gpu_layers_offloaded(),
        "content_chars": len(content),
    }
    print(
        f"  {phase}: wall={wall:.1f}s  load={load_ms:.0f}ms  prefill={ppre:.0f}ms  "
        f"eval={eval_ms:.0f}ms  tok/s={tok_s:.1f}  prompt={p_eval}/{sent}"
        f"{'  [TRUNCATED]' if row['truncated'] else ''}  layers={row['layers_offloaded']}  chars={len(content)}"
    )
    return row


def main() -> int:
    global settings
    settings = get_settings()
    client = ollama.Client(host=settings.ollama_base_url)

    real_messages = _build_real_messages()
    speed_prompt = real_messages or _synthetic_messages()
    speed_tokens = _tokenize(client, settings.ollama_model, _message_text(speed_prompt))
    print(f"speed prompt: {len(speed_prompt)} messages, {speed_tokens} tokens\n")

    big_messages = _synthetic_messages(big=True)
    big_tokens = _tokenize(client, settings.ollama_model, _message_text(big_messages))
    print(f"truncation prompt: {big_tokens} tokens (must exceed 4096)\n")

    rows: list[dict[str, object]] = []
    for ctx in CTX_VALUES:
        print(f"=== speed: num_ctx={ctx} ===")
        gpu_snapshot("before load")
        for phase in ("load", "steady"):
            rows.append(_measure(client, speed_prompt, ctx, speed_tokens, NUM_PREDICT, phase))
            gpu_snapshot(f"after {phase}")
            time.sleep(1)

    for ctx in (4096, 6144):
        print(f"=== truncation check: num_ctx={ctx} (prompt={big_tokens}) ===")
        rows.append(_measure(client, big_messages, ctx, big_tokens, 64, "trunc_check"))
        time.sleep(1)

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_FILE, "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"\nlogged: {OUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
