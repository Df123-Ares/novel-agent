"""Alembic initial schema for phase-1 MVP."""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_phase1"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "books",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("genre", sa.String(64), nullable=False, server_default=""),
        sa.Column("style", sa.String(64), nullable=False, server_default=""),
        sa.Column("length", sa.String(32), nullable=False, server_default="medium"),
        sa.Column("perspective", sa.String(32), nullable=False, server_default="third"),
        sa.Column("tone", sa.String(64), nullable=False, server_default=""),
        sa.Column("premise", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("outline_locked", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("characters_locked", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "outline_nodes",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("book_id", sa.String(32), sa.ForeignKey("books.id"), nullable=False),
        sa.Column("parent_id", sa.String(32), sa.ForeignKey("outline_nodes.id"), nullable=True),
        sa.Column("level", sa.String(32), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_outline_nodes_book_id", "outline_nodes", ["book_id"])
    op.create_table(
        "characters",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("book_id", sa.String(32), sa.ForeignKey("books.id"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("personality", sa.Text(), nullable=False, server_default=""),
        sa.Column("appearance", sa.Text(), nullable=False, server_default=""),
        sa.Column("background", sa.Text(), nullable=False, server_default=""),
        sa.Column("role", sa.String(64), nullable=False, server_default="supporting"),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_characters_book_id", "characters", ["book_id"])
    op.create_table(
        "chapters",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("book_id", sa.String(32), sa.ForeignKey("books.id"), nullable=False),
        sa.Column("outline_node_id", sa.String(32), sa.ForeignKey("outline_nodes.id"), nullable=True),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("goal", sa.Text(), nullable=False, server_default=""),
        sa.Column("target_words", sa.Integer(), nullable=False, server_default="2000"),
        sa.Column("status", sa.String(32), nullable=False, server_default="planned"),
        sa.Column("current_version_id", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_chapters_book_id", "chapters", ["book_id"])
    op.create_table(
        "chapter_versions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("chapter_id", sa.String(32), sa.ForeignKey("chapters.id"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="DRAFT"),
        sa.Column("word_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_chapter_versions_chapter_id", "chapter_versions", ["chapter_id"])
    op.create_table(
        "candidate_change_sets",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("book_id", sa.String(32), sa.ForeignKey("books.id"), nullable=False),
        sa.Column("chapter_id", sa.String(32), sa.ForeignKey("chapters.id"), nullable=False),
        sa.Column("chapter_version_id", sa.String(32), sa.ForeignKey("chapter_versions.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PROPOSED"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_candidate_change_sets_book_id", "candidate_change_sets", ["book_id"])
    op.create_index("ix_candidate_change_sets_chapter_id", "candidate_change_sets", ["chapter_id"])
    op.create_table(
        "candidate_changes",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("change_set_id", sa.String(32), sa.ForeignKey("candidate_change_sets.id"), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("claim", sa.Text(), nullable=False),
        sa.Column("evidence_quote", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("status", sa.String(32), nullable=False, server_default="PROPOSED"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_candidate_changes_change_set_id", "candidate_changes", ["change_set_id"])
    op.create_table(
        "confirmed_facts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("book_id", sa.String(32), sa.ForeignKey("books.id"), nullable=False),
        sa.Column("chapter_id", sa.String(32), sa.ForeignKey("chapters.id"), nullable=False),
        sa.Column("source_change_id", sa.String(32), nullable=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("claim", sa.Text(), nullable=False),
        sa.Column("evidence_quote", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="CONFIRMED"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_confirmed_facts_book_id", "confirmed_facts", ["book_id"])
    op.create_index("ix_confirmed_facts_chapter_id", "confirmed_facts", ["chapter_id"])
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("book_id", sa.String(32), sa.ForeignKey("books.id"), nullable=True),
        sa.Column("chapter_id", sa.String(32), sa.ForeignKey("chapters.id"), nullable=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="QUEUED"),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_tasks_idempotency"),
    )
    op.create_index("ix_tasks_book_id", "tasks", ["book_id"])
    op.create_index("ix_tasks_idempotency_key", "tasks", ["idempotency_key"])
    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("route", sa.String(200), nullable=False),
        sa.Column("response_json", sa.Text(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False, server_default="200"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
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


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS fts_documents")
    op.drop_table("idempotency_keys")
    op.drop_table("tasks")
    op.drop_table("confirmed_facts")
    op.drop_table("candidate_changes")
    op.drop_table("candidate_change_sets")
    op.drop_table("chapter_versions")
    op.drop_table("chapters")
    op.drop_table("characters")
    op.drop_table("outline_nodes")
    op.drop_table("books")
