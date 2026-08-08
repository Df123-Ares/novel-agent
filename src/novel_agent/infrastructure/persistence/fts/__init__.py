"""FTS5 index helpers."""

from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.orm import Session

_CJK = re.compile(r"[\u4e00-\u9fff]")


def _tokenize_for_fts(text_value: str) -> str:
    """Insert spaces around CJK chars so unicode61 FTS can match substrings.

    Non-alphanumeric punctuation is dropped (FTS5 treats characters like quotes
    and operators specially; keeping them would break the MATCH query).
    """
    if not text_value:
        return ""
    parts: list[str] = []
    for ch in text_value:
        if _CJK.match(ch):
            parts.append(ch)
        elif ch.isspace():
            parts.append(" ")
        elif ch.isalnum():
            parts.append(ch)
        else:
            parts.append(" ")
    # Collapse runs of spaces
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def upsert_fts(
    session: Session,
    *,
    doc_id: str,
    book_id: str,
    doc_type: str,
    title: str,
    body: str,
) -> None:
    session.execute(text("DELETE FROM fts_documents WHERE doc_id = :doc_id"), {"doc_id": doc_id})
    session.execute(
        text(
            """
            INSERT INTO fts_documents(doc_id, book_id, doc_type, title, body)
            VALUES (:doc_id, :book_id, :doc_type, :title, :body)
            """
        ),
        {
            "doc_id": doc_id,
            "book_id": book_id,
            "doc_type": doc_type,
            "title": _tokenize_for_fts(title or ""),
            "body": _tokenize_for_fts(body or ""),
        },
    )


def search_fts(session: Session, book_id: str, query: str, *, limit: int = 20) -> list[dict]:
    tokens = _tokenize_for_fts(query).split()
    if not tokens:
        return []
    # AND all tokens; quote each token for literal match
    safe = " ".join(f'"{t}"' for t in tokens)
    rows = session.execute(
        text(
            """
            SELECT doc_id, doc_type, title, body, bm25(fts_documents) AS score
            FROM fts_documents
            WHERE book_id = :book_id AND fts_documents MATCH :q
            ORDER BY score
            LIMIT :limit
            """
        ),
        {"book_id": book_id, "q": safe, "limit": limit},
    ).mappings().all()
    return [dict(r) for r in rows]
