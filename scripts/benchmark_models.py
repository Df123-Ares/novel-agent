"""Benchmark 7b vs 14b model inference speed on local machine.

Tests:
1. Cold start (first call)
2. Warm throughput (tokens/sec)
3. Memory footprint (via ollama ps)
4. Sample chapter generation (real workload)

Usage:
    python scripts/benchmark_models.py
"""

from __future__ import annotations

import json
import time
from typing import Any

import requests

from novel_agent.settings import get_settings

OLLAMA_URL = "http://127.0.0.1:11434"


def check_ollama_running() -> bool:
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def list_models() -> list[str]:
    r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
    return [m["name"] for m in r.json().get("models", [])]


def show_running() -> dict[str, Any] | None:
    """Show currently loaded models + memory footprint."""
    try:
        r = requests.get(f"{OLLAMA_URL}/api/ps", timeout=5)
        if r.status_code == 200:
            data = r.json()
            if data.get("models"):
                m = data["models"][0]
                return {
                    "name": m.get("name"),
                    "size_vram": m.get("size_vram", 0) / 1024**3,
                    "size": m.get("size", 0) / 1024**3,
                    "vram_percent": (
                        m.get("size_vram", 0) / m.get("size", 1) * 100
                        if m.get("size")
                        else 0
                    ),
                }
    except Exception as e:
        print(f"  [warn] ps query failed: {e}")
    return None


def generate(
    model: str,
    prompt: str,
    *,
    num_predict: int = 512,
    num_ctx: int = 4096,
    temperature: float = 0.7,
) -> tuple[str, float, float, dict[str, Any]]:
    """Call ollama generate API, return (text, elapsed_sec, tokens_sec, stats)."""
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_predict": num_predict,
            "num_ctx": num_ctx,
            "temperature": temperature,
            "top_p": 0.9,
            "top_k": 40,
            "repeat_penalty": 1.18,
        },
    }
    t0 = time.perf_counter()
    r = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=600)
    elapsed = time.perf_counter() - t0
    r.raise_for_status()
    data = r.json()
    text = data.get("response", "")
    eval_count = data.get("eval_count", 0)
    eval_duration_s = data.get("eval_duration", 0) / 1e9
    load_duration_s = data.get("load_duration", 0) / 1e9
    tokens_sec = eval_count / eval_duration_s if eval_duration_s > 0 else 0
    stats = {
        "eval_count": eval_count,
        "eval_duration_s": round(eval_duration_s, 2),
        "load_duration_s": round(load_duration_s, 2),
        "total_duration_s": round(elapsed, 2),
    }
    return text, elapsed, tokens_sec, stats


SHORT_PROMPT = "用中文写一段200字的悬疑开场，主角叫林澄，在旧仓库发现撕页日志。"


def benchmark_model(model: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  Benchmarking: {model}")
    print(f"{'=' * 60}")

    # 1. Cold start + short generation
    print(f"\n[1/3] Cold start (short prompt, 512 tokens)...")
    text, elapsed, tps, stats = generate(
        model, SHORT_PROMPT, num_predict=512, num_ctx=4096
    )
    print(f"  Load time:   {stats['load_duration_s']}s")
    print(f"  Eval time:   {stats['eval_duration_s']}s")
    print(f"  Total time:  {stats['total_duration_s']}s")
    print(f"  Tokens:      {stats['eval_count']}")
    print(f"  Speed:       {tps:.1f} tokens/sec")

    # Show VRAM usage
    ps = show_running()
    if ps:
        print(f"  VRAM usage:  {ps['size_vram']:.2f} GB / {ps['size']:.2f} GB "
              f"({ps['vram_percent']:.0f}% on GPU)")

    # 2. Warm throughput (longer generation)
    print(f"\n[2/3] Warm throughput (1024 tokens)...")
    text2, elapsed2, tps2, stats2 = generate(
        model, SHORT_PROMPT, num_predict=1024, num_ctx=4096
    )
    print(f"  Total time:  {stats2['total_duration_s']}s")
    print(f"  Tokens:      {stats2['eval_count']}")
    print(f"  Speed:       {tps2:.1f} tokens/sec")

    # 3. Sample output preview
    print(f"\n[3/3] Sample output (first 300 chars):")
    print(f"  {text[:300]}...")

    # Summary
    print(f"\n--- {model} Summary ---")
    print(f"  Cold start: {stats['total_duration_s']}s (load {stats['load_duration_s']}s)")
    print(f"  Warm speed: {tps2:.1f} tokens/sec")
    if ps:
        print(f"  VRAM:       {ps['size_vram']:.2f} GB on GPU / {ps['size']:.2f} GB total")


def main() -> int:
    if not check_ollama_running():
        print("ERROR: Ollama not running at http://127.0.0.1:11434")
        print("Start it with: ollama serve")
        return 1

    models = list_models()
    print(f"Available models: {models}")

    targets = [m for m in ["qwen2.5:7b-instruct", "qwen2.5:14b-instruct"] if m in models]
    if not targets:
        print("ERROR: No target models found. Pull them first:")
        print("  ollama pull qwen2.5:7b-instruct")
        print("  ollama pull qwen2.5:14b-instruct")
        return 1

    print(f"\nWill benchmark: {targets}")
    for model in targets:
        benchmark_model(model)

    print("\n" + "=" * 60)
    print("  Benchmark complete. Compare the numbers above.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
