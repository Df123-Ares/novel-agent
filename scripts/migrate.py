#!/usr/bin/env python3
"""Run database migrations (create schema)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from novel_agent.bootstrap import ensure_data_dirs
from novel_agent.infrastructure.persistence.db import create_all_tables, get_engine
from novel_agent.settings import get_settings


def main() -> int:
    settings = ensure_data_dirs(get_settings())
    engine = get_engine(settings)
    create_all_tables(engine)
    # Align alembic version table with create_all schema for existing DBs
    stamp = subprocess.run(
        [sys.executable, "-m", "alembic", "stamp", "head"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if stamp.returncode != 0:
        print(stamp.stderr or stamp.stdout)
        print("(stamp skipped/failed; schema still created via create_all)")
    print(f"schema ready: {settings.database_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
