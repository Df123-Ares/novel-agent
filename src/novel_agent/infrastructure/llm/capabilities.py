"""Model capability probe results."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from novel_agent.infrastructure.llm.ollama_adapter import OllamaAdapter


class ProbeCheck(BaseModel):
    name: str
    passed: bool
    detail: str = ""
    degraded: bool = False


class ProbeReport(BaseModel):
    model: str
    base_url: str
    think: bool
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    checks: list[ProbeCheck] = Field(default_factory=list)
    ok: bool = False

    def summarize(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "model": self.model,
            "think": self.think,
            "passed": [c.name for c in self.checks if c.passed],
            "failed": [c.name for c in self.checks if not c.passed],
            "degraded": [c.name for c in self.checks if c.degraded],
        }


class _Ping(BaseModel):
    ok: bool
    msg: str


def probe_model(adapter: OllamaAdapter, *, think: bool | None = None) -> ProbeReport:
    """Run reachability / chat / stream / structured-output checks."""
    think_flag = adapter.think if think is None else think
    report = ProbeReport(
        model=adapter.model,
        base_url=adapter.settings.ollama_base_url,
        think=think_flag,
    )

    # 1. list models / reachability
    try:
        models = adapter.list_models()
        present = any(adapter.model == m or adapter.model in m for m in models)
        report.checks.append(
            ProbeCheck(
                name="reachability",
                passed=True,
                detail=f"models={models}",
            )
        )
        report.checks.append(
            ProbeCheck(
                name="model_present",
                passed=present,
                detail=f"looking for {adapter.model}",
            )
        )
    except Exception as exc:  # noqa: BLE001
        report.checks.append(
            ProbeCheck(name="reachability", passed=False, detail=str(exc))
        )
        report.ok = False
        return report

    # 2. plain chat
    try:
        result = adapter.chat(
            [{"role": "user", "content": "只回复一个词：OK"}],
            think=think_flag,
            num_predict=32,
        )
        chat_ok = bool(result.content.strip())
        report.checks.append(
            ProbeCheck(
                name="chat",
                passed=chat_ok,
                detail=f"content={result.content[:80]!r} thinking_len={len(result.thinking or '')}",
            )
        )
    except Exception as exc:  # noqa: BLE001
        report.checks.append(ProbeCheck(name="chat", passed=False, detail=str(exc)))

    # 3. stream (degraded if fails)
    try:
        chunks: list[str] = []
        for piece in adapter.chat_stream(
            [{"role": "user", "content": "只回复：OK"}],
            think=think_flag,
            num_predict=16,
        ):
            chunks.append(piece)
            if len("".join(chunks)) > 8:
                break
        stream_ok = bool("".join(chunks).strip())
        report.checks.append(
            ProbeCheck(
                name="stream",
                passed=stream_ok,
                detail=f"chunk_count={len(chunks)}",
                degraded=not stream_ok,
            )
        )
    except Exception as exc:  # noqa: BLE001
        report.checks.append(
            ProbeCheck(name="stream", passed=False, detail=str(exc), degraded=True)
        )

    # 4. structured output + pydantic
    try:
        result = adapter.chat(
            [{"role": "user", "content": "返回 JSON：ok=true, msg=hello"}],
            format=_Ping,
            think=think_flag,
            num_predict=128,
            temperature=0,
        )
        if not result.content.strip():
            report.checks.append(
                ProbeCheck(
                    name="structured_output",
                    passed=False,
                    detail="empty content (check think=False for reasoning models)",
                )
            )
        else:
            parsed = _Ping.model_validate_json(result.content)
            report.checks.append(
                ProbeCheck(
                    name="structured_output",
                    passed=parsed.ok is True,
                    detail=result.content[:200],
                )
            )
    except Exception as exc:  # noqa: BLE001
        report.checks.append(
            ProbeCheck(name="structured_output", passed=False, detail=str(exc))
        )

    critical = {"reachability", "model_present", "chat", "structured_output"}
    report.ok = all(
        c.passed for c in report.checks if c.name in critical
    )
    return report
