"""Application settings loaded from environment / .env."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ollama_base_url: str = Field(default="http://127.0.0.1:11434", alias="OLLAMA_BASE_URL")
    ollama_model: str = Field(default="deepseek-r1:8b", alias="OLLAMA_MODEL")
    # deepseek-r1/qwen3 均天然带推理；为结构化 JSON 输出统一关闭（content 保持纯净）
    ollama_think: bool = Field(default=False, alias="OLLAMA_THINK")

    # num_ctx passed to ollama for every call. 6GB VRAM + 5.2GB model:
    # 6144 covers observed prompt+output totals (max 5837) at ~1 GPU-layer cost;
    # 4096 (ollama default) silently truncates prompts > 4096 tokens.
    context_limit: int = Field(default=6144, alias="CONTEXT_LIMIT")
    default_num_predict: int = Field(default=2048, alias="DEFAULT_NUM_PREDICT")
    schema_repair_retries: int = Field(default=2, alias="SCHEMA_REPAIR_RETRIES")

    prompts_dir: Path = Field(default=PROJECT_ROOT / "prompts", alias="PROMPTS_DIR")
    probe_report_dir: Path = Field(
        default=PROJECT_ROOT / "data" / "probe_reports",
        alias="PROBE_REPORT_DIR",
    )
    generation_run_dir: Path = Field(
        default=PROJECT_ROOT / "data" / "generation_runs",
        alias="GENERATION_RUN_DIR",
    )
    database_url: str = Field(
        default=f"sqlite:///{(PROJECT_ROOT / 'data' / 'novel_agent.db').as_posix()}",
        alias="DATABASE_URL",
    )
    # target words for short / medium / long books (used in chapter planning)
    words_short: int = Field(default=30000, alias="WORDS_SHORT")
    words_medium: int = Field(default=120000, alias="WORDS_MEDIUM")
    words_long: int = Field(default=300000, alias="WORDS_LONG")
    prev_chapter_tail_chars: int = Field(default=1500, alias="PREV_CHAPTER_TAIL_CHARS")
    max_facts_in_context: int = Field(default=50, alias="MAX_FACTS_IN_CONTEXT")
    # writing length controls
    writer_num_predict_floor: int = Field(default=4096, alias="WRITER_NUM_PREDICT_FLOOR")
    writer_num_predict_ceil: int = Field(default=12288, alias="WRITER_NUM_PREDICT_CEIL")
    writer_min_words_ratio: float = Field(default=0.55, alias="WRITER_MIN_WORDS_RATIO")
    writer_repair_repetition: bool = Field(default=True, alias="WRITER_REPAIR_REPETITION")
    # max expand/rewrite rounds after the first draft
    writer_max_repair: int = Field(default=2, alias="WRITER_MAX_REPAIR")
    # if repetition trim removes >= this fraction, treat as severe and rewrite
    writer_max_trim_ratio: float = Field(default=0.25, alias="WRITER_MAX_TRIM_RATIO")
    writer_enforce_quality_on_confirm: bool = Field(
        default=True, alias="WRITER_ENFORCE_QUALITY_ON_CONFIRM"
    )
    export_dir: Path = Field(
        default=PROJECT_ROOT / "data" / "exports",
        alias="EXPORT_DIR",
    )


def get_settings() -> Settings:
    return Settings()
