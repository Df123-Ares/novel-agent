"""Prompt registry: load YAML metadata + Jinja2 templates."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel, Field

from novel_agent.settings import Settings, get_settings


class PromptSpec(BaseModel):
    prompt_id: str
    version: str
    purpose: str = ""
    output_schema: str | None = None
    template_file: str
    default_params: dict[str, Any] = Field(default_factory=dict)
    system: str | None = None


class PromptRegistry:
    def __init__(self, prompts_dir: Path | None = None, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.prompts_dir = prompts_dir or self.settings.prompts_dir
        self._env = Environment(
            loader=FileSystemLoader(str(self.prompts_dir)),
            undefined=StrictUndefined,
            autoescape=False,
        )
        self._cache: dict[str, PromptSpec] = {}

    def load(self, relative_yaml: str) -> PromptSpec:
        if relative_yaml in self._cache:
            return self._cache[relative_yaml]
        path = self.prompts_dir / relative_yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        spec = PromptSpec.model_validate(data)
        self._cache[relative_yaml] = spec
        return spec

    def render(self, relative_yaml: str, **kwargs: Any) -> tuple[PromptSpec, list[dict[str, str]]]:
        spec = self.load(relative_yaml)
        template = self._env.get_template(spec.template_file)
        user_content = template.render(**kwargs)
        messages: list[dict[str, str]] = []
        if spec.system:
            messages.append({"role": "system", "content": spec.system})
        messages.append({"role": "user", "content": user_content})
        return spec, messages
