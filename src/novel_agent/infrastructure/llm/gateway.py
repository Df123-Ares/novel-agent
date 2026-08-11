"""LLM gateway: structured chat with schema validation and repair retries."""

from __future__ import annotations

import time
from typing import Any, TypeVar

import ollama
from pydantic import BaseModel, ValidationError

from novel_agent.infrastructure.llm.ollama_adapter import ChatResult, OllamaAdapter
from novel_agent.infrastructure.llm.repair import try_repair_json
from novel_agent.infrastructure.llm.stats import record_llm_call
from novel_agent.settings import Settings, get_settings

T = TypeVar("T", bound=BaseModel)

# transient/transport error namespaces that are safe to retry
_TRANSPORT_MODULES = ("httpx", "requests", "urllib3", "httpcore", "socket", "ssl", "urllib")

# extra attempts beyond the first call for transport failures
_TRANSPORT_RETRIES = 2
_TRANSPORT_BACKOFF = 1.0  # seconds, doubles per attempt


def _is_transient_error(exc: Exception) -> bool:
    if isinstance(exc, ollama.ResponseError):
        return exc.status_code is None or exc.status_code >= 500 or exc.status_code == 429
    return type(exc).__module__.split(".")[0] in _TRANSPORT_MODULES


def _classify_failure(error: Exception) -> str:
    """Classify a validation failure to tailor the repair instruction."""
    message = str(error)
    if "EOF" in message or "Unterminated string" in message or "unterminated string" in message:
        return "truncated"
    if "Extra data" in message or "Expecting value" in message:
        return "garbage"
    return "schema"


def _call_with_transport_retry(
    adapter: OllamaAdapter, messages: list[dict[str, str]], **kwargs: Any
) -> ChatResult:
    """Invoke adapter.chat, retrying transient transport/HTTP failures with backoff."""
    for attempt in range(_TRANSPORT_RETRIES + 1):
        try:
            return adapter.chat(messages, **kwargs)
        except Exception as exc:
            if not _is_transient_error(exc):
                raise
            if attempt >= _TRANSPORT_RETRIES:
                raise
            time.sleep(_TRANSPORT_BACKOFF * (2**attempt))
    raise RuntimeError("unreachable")


def _repair_instruction(error: Exception) -> str:
    """Build a failure-specific repair instruction for the model retry."""
    kind = _classify_failure(error)
    if kind == "truncated":
        return (
            "你的输出在 JSON 中途被截断：某个字符串没有闭合，输出戛然而止。"
            "请重新输出一份完整、闭合的 JSON：所有引号和括号必须成对，"
            "宁可内容略短，也不要中途截断。只输出 JSON，不要解释。"
        )
    if kind == "garbage":
        return (
            "你的输出混入了非 JSON 内容（如前言、代码块或多份 JSON）。"
            "请只输出一份合法 JSON，不要任何解释或多余文字。"
        )
    return (
        "上一次输出未通过 Schema 校验。"
        f"错误：{error}\n"
        "请只输出符合 JSON Schema 的合法 JSON，不要解释。"
    )


def _raw_metrics(result: ChatResult | None) -> tuple[int | None, int | None, float | None]:
    """Extract prompt/eval token counts and total duration from the raw response."""
    if result is None:
        return None, None, None
    raw = result.raw or {}
    prompt_eval = raw.get("prompt_eval_count")
    eval_count = raw.get("eval_count")
    total_ns = raw.get("total_duration")
    return (
        int(prompt_eval) if isinstance(prompt_eval, (int, float)) else None,
        int(eval_count) if isinstance(eval_count, (int, float)) else None,
        float(total_ns) / 1e6 if isinstance(total_ns, (int, float)) else None,
    )


class LLMGateway:
    def __init__(
        self,
        adapter: OllamaAdapter | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.adapter = adapter or OllamaAdapter(self.settings)

    def chat_text(
        self,
        messages: list[dict[str, str]],
        *,
        num_predict: int | None = None,
        temperature: float | None = None,
    ) -> ChatResult:
        start = time.perf_counter()
        success = False
        result: ChatResult | None = None
        try:
            result = self.adapter.chat(
                messages,
                num_predict=num_predict,
                temperature=temperature,
            )
            success = True
            return result
        finally:
            prompt_eval, eval_count, total_ms = _raw_metrics(result)
            record_llm_call(
                schema="<text>",
                attempts=1,
                success=success,
                duration_ms=(time.perf_counter() - start) * 1000,
                content_chars=len(result.content) if result is not None else 0,
                prompt_eval_count=prompt_eval,
                eval_count=eval_count,
                total_duration_ms=total_ms,
                rule_repaired=False,
            )

    def chat_structured(
        self,
        messages: list[dict[str, str]],
        schema: type[T],
        *,
        num_predict: int | None = None,
        temperature: float | None = 0.3,
        repair_retries: int | None = None,
    ) -> tuple[T, ChatResult]:
        retries = (
            self.settings.schema_repair_retries
            if repair_retries is None
            else repair_retries
        )
        start = time.perf_counter()
        attempts = 0
        success = False
        rule_repaired = False
        last_result: ChatResult | None = None
        last_error: Exception | None = None
        working_messages = list(messages)
        attempts_detail: list[dict[str, Any]] = []

        try:
            for attempt in range(retries + 1):
                attempts += 1
                result = _call_with_transport_retry(
                    self.adapter,
                    working_messages,
                    format=schema,
                    num_predict=num_predict,
                    temperature=temperature,
                )
                last_result = result
                prompt_eval, eval_count, _ = _raw_metrics(result)
                detail: dict[str, Any] = {
                    "num_predict": num_predict,
                    "eval_count": eval_count,
                    "content_chars": len(result.content),
                    "cap_hit": eval_count is not None and num_predict is not None and eval_count >= num_predict,
                }
                attempts_detail.append(detail)
                try:
                    if not result.content.strip():
                        raise ValueError("empty model content (check OLLAMA_THINK=false for reasoning models)")
                    parsed = schema.model_validate_json(result.content)
                    success = True
                    return parsed, result
                except (ValidationError, ValueError) as exc:
                    repaired = try_repair_json(result.content, schema)
                    if repaired is not None:
                        rule_repaired = True
                        success = True
                        return repaired, result
                    last_error = exc
                    detail["error"] = _classify_failure(exc)
                    working_messages = list(messages) + [
                        {"role": "assistant", "content": result.content or ""},
                        {
                            "role": "user",
                            "content": _repair_instruction(exc),
                        },
                    ]

            assert last_error is not None
            raise last_error
        finally:
            prompt_eval, eval_count, total_ms = _raw_metrics(last_result)
            record_llm_call(
                schema=schema.__name__,
                attempts=attempts,
                success=success,
                duration_ms=(time.perf_counter() - start) * 1000,
                content_chars=len(last_result.content) if last_result is not None else 0,
                prompt_eval_count=prompt_eval,
                eval_count=eval_count,
                total_duration_ms=total_ms,
                rule_repaired=rule_repaired,
                attempts_detail=attempts_detail,
            )
