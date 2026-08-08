"""Data models shared by task coordinator implementations."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple


class TaskStatus(str, Enum):
    """Lifecycle states persisted for an agent task."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        """Return whether no further work may run for this status."""

        return self in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }


class RejectionReason(str, Enum):
    """Stable reasons returned when a task cannot be admitted."""

    DUPLICATE_TASK_ID = "duplicate_task_id"
    CHAT_ALREADY_RUNNING = "chat_already_running"
    QUEUE_FULL = "queue_full"
    INVALID_REQUEST = "invalid_request"


@dataclass(frozen=True, slots=True)
class TaskMetadata:
    """Portable metadata required to coordinate one active task."""

    task_id: str
    user_id: str
    chat_id: str
    status: TaskStatus


@dataclass(frozen=True, slots=True)
class AdmissionResult:
    """Result of attempting to admit a new task."""

    accepted: bool
    status: Optional[TaskStatus] = None
    reason: Optional[RejectionReason] = None
    conflicting_task_id: Optional[str] = None


@dataclass(frozen=True, slots=True)
class FinishResult:
    """Result of terminal cleanup and any queue promotions it unlocked."""

    finished: bool
    promoted_task_ids: Tuple[str, ...] = ()
