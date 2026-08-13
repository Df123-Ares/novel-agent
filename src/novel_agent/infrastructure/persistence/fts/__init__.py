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


def search_relevant_facts(
    session: Session,
    book_id: str,
    query: str,
    *,
    limit: int = 30,
    max_tokens: int = 16,
) -> list[str]:
    """Retrieve fact_ids relevant to a query using FTS5 OR semantics + bm25 ranking.

    Designed for long-novel RAG: given a chapter goal/title, return the most
    relevant confirmed-fact ids so `_build_chapter_context` can inject them
    instead of the naive "last N facts" slice.

    - OR query: any token match counts (AND would over-filter on long Chinese queries)
    - bm25 ranks by relevance (lower score = better match in SQLite FTS5)
    - Chinese is tokenized char-by-char by `_tokenize_for_fts`, so each CJK
      character becomes a single token. We keep single-char tokens (they carry
      meaning in Chinese: 林/澄/仓/库) but drop common stop-words (的/了/在/是)
      to avoid matching every document.
    - Tokens are deduplicated and capped to avoid exploding the MATCH expression

    Returns fact_ids without the 'fact:' prefix, ordered by relevance.
    """
    # Common Chinese function words / particles that appear in almost every
    # document and carry no discriminative signal for bm25 ranking.
    stop_words = {
        # 助词 / 语气词
        "的", "了", "着", "过", "吗", "呢", "吧", "啊", "呀", "哦", "嗯",
        "罢", "哉",
        # 系词 / 判断
        "是", "乃", "系",
        # 介词
        "在", "于", "为", "由", "从", "向", "往", "照", "依", "凭", "经",
        "以", "用", "把", "被", "让", "使", "给", "对", "替", "连",
        # 连词
        "和", "与", "及", "或", "但", "而", "且", "故", "因", "虽", "若",
        "则", "如", "并",
        # 否定 / 副词
        "不", "也", "都", "就", "还", "又", "已", "曾", "正", "才", "只",
        "便", "即", "方", "将",
        # 方位 / 处所
        "上", "下", "中", "里", "外", "前", "后", "内", "旁", "间", "侧",
        # 代词
        "他", "她", "你", "我", "它", "这", "那", "此", "某", "谁", "该",
        "其", "之",
        # 量词 / 数词
        "一", "个", "们", "到", "来", "去", "些", "样",
        # 高频叙事动词（实义弱、区分度低）
        "说", "道", "看", "听", "想",
    }
    tokens = _tokenize_for_fts(query).split()
    if not tokens:
        return []
    # Deduplicate while preserving order; drop stop-words
    seen: set[str] = set()
    unique_tokens: list[str] = []
    for t in tokens:
        if t in stop_words or t in seen:
            continue
        seen.add(t)
        unique_tokens.append(t)
        if len(unique_tokens) >= max_tokens:
            break
    if not unique_tokens:
        return []
    # OR query: any token match counts; bm25 ranks by relevance
    safe = " OR ".join(f'"{t}"' for t in unique_tokens)
    rows = session.execute(
        text(
            """
            SELECT doc_id
            FROM fts_documents
            WHERE book_id = :book_id
              AND doc_type = 'fact'
              AND fts_documents MATCH :q
            ORDER BY bm25(fts_documents)
            LIMIT :limit
            """
        ),
        {"book_id": book_id, "q": safe, "limit": limit},
    ).all()
    result: list[str] = []
    for row in rows:
        doc_id = row[0]
        if doc_id.startswith("fact:"):
            result.append(doc_id[len("fact:"):])
    return result
