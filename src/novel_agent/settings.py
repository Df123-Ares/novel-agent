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
        populate_by_name=True,
    )

    ollama_base_url: str = Field(default="http://127.0.0.1:11434", alias="OLLAMA_BASE_URL")
    ollama_model: str = Field(default="qwen2.5:7b-instruct", alias="OLLAMA_MODEL")
    # deepseek-r1/qwen3 均天然带推理；为结构化 JSON 输出统一关闭（content 保持纯净）
    ollama_think: bool = Field(default=False, alias="OLLAMA_THINK")

    # ── GPU / 推理加速 ──────────────────────────────────────────────────────
    # GPU 层数：qwen2.5-7b 共 28 层，-1=all，6GB VRAM 勉强塞下 28 层 Q4（~4.7GB）；
    # 若频繁 swap/OOM，降为 26-27 层留显存缓冲。0 = 纯 CPU 推理。
    ollama_num_gpu: int = Field(default=-1, alias="OLLAMA_NUM_GPU")

    # num_ctx passed to ollama for every call. 6GB VRAM + 5.2GB model:
    # 6144 covers observed prompt+output totals (max 5837) at ~1 GPU-layer cost;
    # 4096 (ollama default) silently truncates prompts > 4096 tokens.
    context_limit: int = Field(default=6144, alias="CONTEXT_LIMIT")
    default_num_predict: int = Field(default=2048, alias="DEFAULT_NUM_PREDICT")
    # 1 次 schema 修复重试已足够；2 次会让单章耗时翻倍而收益有限。
    schema_repair_retries: int = Field(default=1, alias="SCHEMA_REPAIR_RETRIES")

    # ── 采样参数（速度 + 质量双控）──────────────────────────────────────────
    # temperature 越低越稳定/越快（发散少 → 扩写/重写少），但过低词汇贫乏。
    # 网文写作 0.65-0.7 是速度/质量平衡点。
    temperature: float = Field(default=0.65, alias="TEMPERATURE")
    # top_k/top_p 控制候选词多样性，Ollama 默认 top_k=40, top_p=0.9。
    top_k: int = Field(default=40, alias="TOP_K")
    top_p: float = Field(default=0.9, alias="TOP_P")
    # tfs_z (Tail Free Sampling) 削除低概率长尾候选，减少无效计算 → 略提速 + 抑重复。
    # 0.95 是温和值；进一步加速可试 0.90，但词汇多样性下降。0=禁用。
    tfs_z: float = Field(default=0.95, alias="TFS_Z")
    # min_p 相对概率阈值：候选词概率 < max_prob * min_p 则丢弃。
    # 0.05 砍掉概率<2%的候选词，对质量影响小但可 5-10% 提速。0=禁用。
    min_p: float = Field(default=0.05, alias="MIN_P")
    # repeat_last_n 扩大重复惩罚回看窗口。Ollama 默认 64 对长文太短，
    # 跨段重复逃过惩罚，调到 512 可显著抑制退化循环。-1=整个上下文。
    repeat_last_n: int = Field(default=512, alias="REPEAT_LAST_N")
    # repeat_penalty 默认 1.1；1.18 抑制中文长文退化循环；
    # 过高 1.25+ 会导致词汇贫乏；配合 repeat_last_n=512 跨段惩罚效果更佳。
    repeat_penalty: float = Field(default=1.18, alias="REPEAT_PENALTY")
    # mirostat 动态 perplexity 控制（长文特别有效）：
    #   0=关闭（Ollama 默认），1=Mirostat v1，2=Mirostat v2（推荐）
    # 开启后会动态调整采样熵，避免长文中段质量断崖。对速度影响可忽略。
    ollama_mirostat: int = Field(default=2, alias="OLLAMA_MIROSTAT")
    # mirostat_tau 目标 entropy，2.0=更发散，3.0=更确定；4.0 适合 7b + 0.65 temp。
    ollama_mirostat_tau: float = Field(default=4.0, alias="OLLAMA_MIROSTAT_TAU")

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
    # few-shot 风格示例：从已确认章节中抽取一段正文作为风格参考
    # 0 = 关闭；推荐 600~800（太短无效果，太长挤占输出上下文）
    few_shot_sample_chars: int = Field(default=600, alias="FEW_SHOT_SAMPLE_CHARS")
    # writing length controls
    # floor 调到 3072：2000 字目标约需 3200 tokens 输出，3072 足以覆盖，
    # 同时比 4096 节省 ~25% 生成耗时；context 不足时由 _writer_num_predict 自动收缩。
    writer_num_predict_floor: int = Field(default=3072, alias="WRITER_NUM_PREDICT_FLOOR")
    writer_num_predict_ceil: int = Field(default=12288, alias="WRITER_NUM_PREDICT_CEIL")
    writer_min_words_ratio: float = Field(default=0.5, alias="WRITER_MIN_WORDS_RATIO")
    writer_repair_repetition: bool = Field(default=True, alias="WRITER_REPAIR_REPETITION")
    # 0 轮重写：默认关闭重写循环（每次重写都是 1 次 4096 tokens 慢调用）。
    # 重复问题已通过 repeat_penalty/top_k/top_p/repeat_last_n 在采样端抑制。
    writer_max_repair: int = Field(default=0, alias="WRITER_MAX_REPAIR")
    # if repetition trim removes >= this fraction, treat as severe and rewrite
    writer_max_trim_ratio: float = Field(default=0.25, alias="WRITER_MAX_TRIM_RATIO")
    writer_enforce_quality_on_confirm: bool = Field(
        default=True, alias="WRITER_ENFORCE_QUALITY_ON_CONFIRM"
    )
    # max words per chapter (auto-split if outline goal exceeds this)
    max_chapter_words: int = Field(default=2000, alias="MAX_CHAPTER_WORDS")
    # consistency check during generation (advisory, off by default for speed)
    run_consistency_check: bool = Field(default=False, alias="RUN_CONSISTENCY_CHECK")
    # extractor for candidate facts (advisory, off by default for speed).
    # 默认关闭：每章节省 1 次 LLM 调用；开启 validator 时建议同时开启。
    run_extractor: bool = Field(default=False, alias="RUN_EXTRACTOR")
    # ── 分层摘要（长程一致性核心机制）──────────────────────────────────────
    # 每 ARC_SIZE 章（默认 10）生成一卷摘要，注入到后续章节的写作上下文。
    # 解决长篇剧情遗忘：第 50 章能看到第 1-10、11-20、21-30、31-40 卷的摘要。
    # 每 MEGA_ARC_SIZE 章（默认 50）生成"大卷摘要"，进一步压缩 5 个卷摘要。
    arc_size: int = Field(default=10, alias="ARC_SIZE")
    mega_arc_size: int = Field(default=50, alias="MEGA_ARC_SIZE")
    # 注入到 chapter_context 的最近卷摘要数（默认 5 = 最近 50 章）。
    # 设为 0 关闭分层摘要注入；过大可能撑爆 ctx。
    arc_summaries_in_context: int = Field(default=5, alias="ARC_SUMMARIES_IN_CONTEXT")
    # 注入的最近大卷摘要数（默认 3 = 最近 150 章）；超过此数取最近的。
    mega_arc_summaries_in_context: int = Field(default=3, alias="MEGA_ARC_SUMMARIES_IN_CONTEXT")
    # 卷摘要生成时的 num_predict 上限（约 800 字 ≈ 1200 tokens）。
    arc_summary_num_predict: int = Field(default=1200, alias="ARC_SUMMARY_NUM_PREDICT")
    export_dir: Path = Field(
        default=PROJECT_ROOT / "data" / "exports",
        alias="EXPORT_DIR",
    )


def get_settings() -> Settings:
    return Settings()
