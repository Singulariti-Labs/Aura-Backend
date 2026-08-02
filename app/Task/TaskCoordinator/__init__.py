"""Task admission and coordination primitives.

The coordinator owns only portable task metadata. Process-local runtime objects
such as WebSockets, asyncio Tasks, Events, and Queues remain in
``app.Task.task_manager`` and are intentionally never stored here.
"""

from .base import TaskCoordinator
from .config import TaskCoordinatorSettings
from .factory import create_task_coordinator, task_coordinator
from .memory import InMemoryTaskCoordinator
from .models import (
    AdmissionResult,
    FinishResult,
    RejectionReason,
    TaskMetadata,
    TaskStatus,
)

__all__ = [
    "AdmissionResult",
    "FinishResult",
    "InMemoryTaskCoordinator",
    "RejectionReason",
    "TaskCoordinator",
    "TaskCoordinatorSettings",
    "TaskMetadata",
    "TaskStatus",
    "create_task_coordinator",
    "task_coordinator",
]
