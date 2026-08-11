"""Rule-based repair for structured LLM output, before falling back to model repair.

Handles the common, cheaply fixable failure shapes produced by local models:
code fences / prose wrappers, trailing garbage, and truncation at num_predict.

Any failure returns None and the caller falls back to model-driven repair.
"""

from __future__ import annotations

import json
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

_MAX_TRUNCATION_TRIES = 256


def _extract_json_text(content: str) -> str:
    """Strip code fences and prose wrappers, returning the outermost JSON span."""
    text = content.strip()
    if not text:
        return ""

    open_at = -1
    open_ch = ""
    close_ch = ""
    for i, ch in enumerate(text):
        if ch in "[{":
            open_at = i
            open_ch = ch
            close_ch = "}" if ch == "{" else "]"
            break
    if open_at < 0:
        return text

    depth = 0
    in_str = False
    escaped = False
    close_at = -1
    for i in range(open_at, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                close_at = i
                break

    if close_at >= open_at:
        return text[open_at : close_at + 1]
    return text[open_at:]


def _trim_trailing_comma(text: str) -> str:
    stripped = text.rstrip()
    while stripped.endswith(","):
        stripped = stripped[:-1].rstrip()
    return stripped


_CLOSERS = {"[": "]", "{": "}"}


def _bracket_state(text: str) -> tuple[list[str], bool]:
    """Return (open bracket stack, whether scan ends inside a string)."""
    stack: list[str] = []
    in_str = False
    escaped = False
    for ch in text:
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "[{":
            stack.append(ch)
        elif ch in "]}":
            if stack:
                stack.pop()
    return stack, in_str


def _close_candidates(text: str) -> list[str]:
    """Candidate completions of a truncated JSON document."""
    stack, in_str = _bracket_state(text)
    closers = "".join(_CLOSERS[b] for b in reversed(stack))
    candidates = [text + closers]
    if in_str:
        candidates.insert(0, text + '"' + closers)
    return candidates


def _valid_prefix(text: str) -> str | None:
    """Find a parseable completion of a truncated JSON document (bounded cost)."""
    candidate = _trim_trailing_comma(text)
    try:
        json.loads(candidate)
        return candidate
    except json.JSONDecodeError:
        pass

    tries = 0
    cut = len(candidate)
    while cut > 0 and tries < _MAX_TRUNCATION_TRIES:
        tries += 1
        head = _trim_trailing_comma(candidate[:cut])
        for cand in _close_candidates(head):
            try:
                json.loads(cand)
                return cand
            except json.JSONDecodeError:
                continue
        cut -= 1
    return None


def _load(data_text: str) -> object | None:
    if not data_text:
        return None
    try:
        parsed: object = json.loads(data_text)
        return parsed
    except json.JSONDecodeError:
        valid = _valid_prefix(data_text)
        if valid is None:
            return None
        try:
            repaired: object = json.loads(valid)
            return repaired
        except json.JSONDecodeError:
            return None


def try_repair_json(content: str, schema: type[T]) -> T | None:
    """Return a schema instance if the raw output is rule-repairable, else None."""
    try:
        data = _load(_extract_json_text(content))
        if data is None:
            return None
        return schema.model_validate(data)
    except Exception:
        return None
