"""Ollama chat adapter with think=False default for reasoning models."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import ollama
from pydantic import BaseModel

from novel_agent.settings import Settings, get_settings


class ChatResult(BaseModel):
    content: str
    thinking: str | None = None
    raw: dict[str, Any] = {}


class OllamaAdapter:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.client = ollama.Client(host=self.settings.ollama_base_url)
        self.model = self.settings.ollama_model
        self.think = self.settings.ollama_think

    def list_models(self) -> list[str]:
        response = self.client.list()
        models = getattr(response, "models", None) or []
        names: list[str] = []
        for m in models:
            name = getattr(m, "model", None) or getattr(m, "name", None)
            if name:
                names.append(str(name))
        return names

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        format: dict[str, Any] | type[BaseModel] | str | None = None,
        think: bool | None = None,
        num_predict: int | None = None,
        temperature: float | None = None,
        model: str | None = None,
    ) -> ChatResult:
        schema: dict[str, Any] | str | None
        if format is None:
            schema = None
        elif isinstance(format, type) and issubclass(format, BaseModel):
            schema = format.model_json_schema()
        else:
            schema = format  # type: ignore[assignment]

        options: dict[str, Any] = {
            "num_predict": num_predict
            if num_predict is not None
            else self.settings.default_num_predict,
            "num_ctx": self.settings.context_limit,
            "num_gpu": self.settings.ollama_num_gpu,
            "temperature": temperature
            if temperature is not None
            else self.settings.temperature,
            "repeat_penalty": self.settings.repeat_penalty,
            # 采样端抑制重复：top_k/top_p 控制多样性，repeat_last_n 扩大惩罚回看窗口。
            # 默认 repeat_last_n=64 对长文太短，跨段重复逃过惩罚。
            "top_k": self.settings.top_k,
            "top_p": self.settings.top_p,
            "repeat_last_n": self.settings.repeat_last_n,
        }
        # tfs_z / min_p 非零值才传（0 代表禁用，与省略不等价：省略=用模型默认）
        if self.settings.tfs_z > 0:
            options["tfs_z"] = self.settings.tfs_z
        if self.settings.min_p > 0:
            options["min_p"] = self.settings.min_p
        # mirostat 采样：v2 对长文质量稳定性有明显提升，速度影响可忽略。
        if self.settings.ollama_mirostat > 0:
            options["mirostat"] = self.settings.ollama_mirostat
            options["mirostat_tau"] = self.settings.ollama_mirostat_tau

        kwargs: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "options": options,
            "think": self.think if think is None else think,
        }
        if schema is not None:
            kwargs["format"] = schema

        response = self.client.chat(**kwargs)
        message = response.message
        content = getattr(message, "content", None) or ""
        thinking = getattr(message, "thinking", None)
        return ChatResult(
            content=content,
            thinking=thinking,
            raw=response.model_dump() if hasattr(response, "model_dump") else {},
        )

    def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        think: bool | None = None,
        num_predict: int | None = None,
        model: str | None = None,
    ) -> Iterator[str]:
        options: dict[str, Any] = {
            "num_predict": num_predict
            if num_predict is not None
            else self.settings.default_num_predict,
            "num_ctx": self.settings.context_limit,
            "num_gpu": self.settings.ollama_num_gpu,
            "temperature": self.settings.temperature,
            "repeat_penalty": self.settings.repeat_penalty,
            "top_k": self.settings.top_k,
            "top_p": self.settings.top_p,
            "repeat_last_n": self.settings.repeat_last_n,
        }
        if self.settings.tfs_z > 0:
            options["tfs_z"] = self.settings.tfs_z
        if self.settings.min_p > 0:
            options["min_p"] = self.settings.min_p
        if self.settings.ollama_mirostat > 0:
            options["mirostat"] = self.settings.ollama_mirostat
            options["mirostat_tau"] = self.settings.ollama_mirostat_tau
        stream = self.client.chat(
            model=model or self.model,
            messages=messages,
            stream=True,
            think=self.think if think is None else think,
            options=options,
        )
        for chunk in stream:
            message = chunk.message
            piece = getattr(message, "content", None) or ""
            if piece:
                yield piece
