from __future__ import annotations

from logging.config import fileConfig
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from novel_agent.infrastructure.persistence.models import Base
from novel_agent.settings import get_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

# FTS5 virtual table + its runtime shadow tables are not part of Base.metadata.
# Excluding them keeps `alembic check` from reporting spurious removals.
_FTS_SHADOW = {
    "fts_documents",
    "fts_documents_content",
    "fts_documents_data",
    "fts_documents_docsize",
    "fts_documents_config",
    "fts_documents_idx",
}


def _include_name(name: str, type_: str, parent_names) -> bool:  # noqa: ANN001
    return not (type_ == "table" and name in _FTS_SHADOW)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        include_name=_include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            include_name=_include_name,
        )
        with context.begin_transaction():
            context.run_migrations()
        # Ensure FTS virtual table exists after upgrade
        connection.execute(
            text(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS fts_documents USING fts5(
                    doc_id UNINDEXED,
                    book_id UNINDEXED,
                    doc_type UNINDEXED,
                    title,
                    body,
                    tokenize = 'unicode61'
                );
                """
            )
        )
        connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
