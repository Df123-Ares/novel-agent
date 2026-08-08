"""Domain exceptions and shared helpers."""

from __future__ import annotations

from uuid import uuid4


def new_id() -> str:
    return uuid4().hex


class AppError(Exception):
    code: str = "APP_ERROR"
    status_code: int = 400

    def __init__(self, message: str, *, code: str | None = None, details: dict | None = None):
        super().__init__(message)
        if code:
            self.code = code
        self.message = message
        self.details = details or {}


class NotFoundError(AppError):
    code = "NOT_FOUND"
    status_code = 404


class ConflictError(AppError):
    code = "VERSION_CONFLICT"
    status_code = 409


class PreconditionError(AppError):
    code = "PRECONDITION_FAILED"
    status_code = 412
