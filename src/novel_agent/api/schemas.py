"""Unified API response helpers."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class Meta(BaseModel):
    trace_id: str = Field(default_factory=lambda: uuid4().hex)
    version: int | None = None


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ApiResponse(BaseModel):
    success: bool
    data: Any = None
    error: ErrorBody | None = None
    meta: Meta = Field(default_factory=Meta)


def ok(data: Any = None, *, version: int | None = None, trace_id: str | None = None) -> dict:
    meta = Meta(trace_id=trace_id or uuid4().hex, version=version)
    return ApiResponse(success=True, data=data, meta=meta).model_dump()


def fail(
    code: str,
    message: str,
    *,
    details: dict | None = None,
    trace_id: str | None = None,
) -> dict:
    meta = Meta(trace_id=trace_id or uuid4().hex)
    return ApiResponse(
        success=False,
        error=ErrorBody(code=code, message=message, details=details or {}),
        meta=meta,
    ).model_dump()
