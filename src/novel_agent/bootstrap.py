"""Bootstrap helpers for scripts and future API entrypoints."""

from __future__ import annotations

from novel_agent.settings import PROJECT_ROOT, Settings, get_settings


def ensure_data_dirs(settings: Settings | None = None) -> Settings:
    settings = settings or get_settings()
    settings.probe_report_dir.mkdir(parents=True, exist_ok=True)
    settings.generation_run_dir.mkdir(parents=True, exist_ok=True)
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "data").mkdir(parents=True, exist_ok=True)
    return settings
