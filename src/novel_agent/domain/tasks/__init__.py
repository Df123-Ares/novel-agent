"""Task status machine (phase 0: in-memory only)."""

from __future__ import annotations

from enum import Enum


class TaskStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RETRYABLE = "RETRYABLE"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELED = "CANCELED"
    INTERRUPTED = "INTERRUPTED"


# Phase-0 allowed transitions (approval path reserved, not required)
ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.QUEUED: {TaskStatus.RUNNING, TaskStatus.CANCELED},
    TaskStatus.RUNNING: {
        TaskStatus.WAITING_APPROVAL,
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.RETRYABLE,
        TaskStatus.CANCEL_REQUESTED,
        TaskStatus.INTERRUPTED,
    },
    TaskStatus.WAITING_APPROVAL: {TaskStatus.RUNNING, TaskStatus.CANCELED, TaskStatus.FAILED},
    TaskStatus.CANCEL_REQUESTED: {TaskStatus.CANCELED},
    TaskStatus.RETRYABLE: {TaskStatus.QUEUED, TaskStatus.FAILED},
    TaskStatus.SUCCEEDED: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.CANCELED: set(),
    TaskStatus.INTERRUPTED: {TaskStatus.RETRYABLE, TaskStatus.FAILED},
}


def can_transition(current: TaskStatus, target: TaskStatus) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, set())
