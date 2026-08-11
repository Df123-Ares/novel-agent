"""Unit tests for rule-based JSON repair (infrastructure/llm/repair.py)."""

from __future__ import annotations

from pydantic import BaseModel

from novel_agent.infrastructure.llm.repair import try_repair_json


class BookCard(BaseModel):
    title: str
    genre: str
    themes: list[str]


VALID = '{"title": "测试之书", "genre": "仙侠", "themes": ["重生", "宗门"]}'


def test_direct_valid() -> None:
    out = try_repair_json(VALID, BookCard)
    assert out is not None
    assert out.title == "测试之书"


def test_code_fence_wrapped() -> None:
    raw = '```json\n' + VALID + '\n```'
    out = try_repair_json(raw, BookCard)
    assert out is not None
    assert out.genre == "仙侠"


def test_prose_wrapper() -> None:
    raw = "好的，以下是结果：\n" + VALID + "\n希望有帮助！"
    out = try_repair_json(raw, BookCard)
    assert out is not None
    assert out.themes == ["重生", "宗门"]


def test_truncated_json_prefix() -> None:
    truncated = VALID[:-6]  # cut mid "themes" array
    out = try_repair_json(truncated, BookCard)
    assert out is not None
    assert out.title == "测试之书"


def test_trailing_comma() -> None:
    raw = '{"title": "测试之书", "genre": "仙侠", "themes": ["重生", "宗门"],}'
    out = try_repair_json(raw, BookCard)
    assert out is not None
    assert out.title == "测试之书"


def test_unterminated_string_truncated() -> None:
    raw = '{"title": "测试之书", "genre": "仙侠", "themes": ["重生"'
    out = try_repair_json(raw, BookCard)
    assert out is not None
    assert out.themes == ["重生"]


def test_partial_truncation_missing_fields_returns_none() -> None:
    # Truncation that drops required fields cannot be rule-repaired (no invention).
    assert try_repair_json('{"title": "测试之书"', BookCard) is None


def test_garbage_returns_none() -> None:
    assert try_repair_json("完全不像是 JSON 的输出", BookCard) is None
    assert try_repair_json("", BookCard) is None


def test_valid_json_wrong_shape_returns_none() -> None:
    wrong_shape = '{"title": "x"}'  # missing required genre/themes: no invention
    assert try_repair_json(wrong_shape, BookCard) is None


def test_empty_after_wrapper_returns_none() -> None:
    assert try_repair_json("```json\n\n```", BookCard) is None
