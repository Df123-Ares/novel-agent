"""LLM gateway: structured chat with schema validation and repair retries."""

from __future__ import annotations

import time
from typing import TypeVar

import ollama
from pydantic import BaseModel, ValidationError

from novel_agent.infrastructure.llm.ollama_adapter import ChatResult, OllamaAdapter
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


def _call_with_transport_retry(
    adapter: OllamaAdapter, messages: list[dict[str, str]], **kwargs: object
) -> ChatResult:
    """Invoke adapter.chat, retrying transient transport/HTTP failures with backoff."""
    for attempt in range(_TRANSPORT_RETRIES + 1):
        try:
            return adapter.chat(messages, **kwargs)
        except Exception as exc:  # noqa: BLE001 - transport/HTTP failure
            if not _is_transient_error(exc):
                raise
            if attempt >= _TRANSPORT_RETRIES:
                raise
            time.sleep(_TRANSPORT_BACKOFF * (2**attempt))
    raise RuntimeError("unreachable")


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
        return self.adapter.chat(
            messages,
            num_predict=num_predict,
            temperature=temperature,
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
        last_error: Exception | None = None
        working_messages = list(messages)

        for attempt in range(retries + 1):
            result = _call_with_transport_retry(
                self.adapter,
                working_messages,
                format=schema,
                num_predict=num_predict,
                temperature=temperature,
            )
            try:
                if not result.content.strip():
                    raise ValueError("empty model content (check OLLAMA_THINK=false for reasoning models)")
                parsed = schema.model_validate_json(result.content)
                return parsed, result
            except (ValidationError, ValueError) as exc:
                last_error = exc
                working_messages = list(messages) + [
                    {"role": "assistant", "content": result.content or ""},
                    {
                        "role": "user",
                        "content": (
                            "上一次输出未通过 Schema 校验。"
                            f"错误：{exc}\n"
                            "请只输出符合 JSON Schema 的合法 JSON，不要解释。"
                        ),
                    },
                ]

        assert last_error is not None
        raise last_error
