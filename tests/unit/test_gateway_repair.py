"""Unit tests for gateway failure classification and repair instructions."""

from __future__ import annotations

from pydantic import BaseModel, ValidationError

from novel_agent.infrastructure.llm.gateway import _classify_failure, _repair_instruction


class Card(BaseModel):
    title: str


def test_classify_truncated() -> None:
    err = ValueError("EOF while parsing a string at line 3 column 4210")
    assert _classify_failure(err) == "truncated"
    assert _classify_failure(ValueError("Unterminated string starting at line 1")) == "truncated"


def test_classify_garbage() -> None:
    assert _classify_failure(ValueError("Extra data: line 2 column 1")) == "garbage"
    assert _classify_failure(ValueError("Expecting value: line 1 column 2")) == "garbage"


def test_classify_schema() -> None:
    try:
        Card.model_validate_json("{}")  # noqa: B018 - expect failure
    except ValidationError as exc:
        assert _classify_failure(exc) == "schema"
    else:  # pragma: no cover
        raise AssertionError("expected ValidationError")


def test_repair_instruction_truncated() -> None:
    text = _repair_instruction(ValueError("EOF while parsing a string"))
    assert "截断" in text
    assert "闭合" in text


def test_repair_instruction_garbage() -> None:
    text = _repair_instruction(ValueError("Extra data"))
    assert "非 JSON" in text


def test_repair_instruction_schema() -> None:
    text = _repair_instruction(ValueError("field required"))
    assert "Schema" in text
