"""Backend-neutral contract for task coordination."""

from abc import ABC, abstractmethod
from typing import Optional

from .models import AdmissionResult, FinishResult, TaskMetadata, TaskStatus


class TaskCoordinator(ABC):
    """Coordinate chat ownership, admission limits, and pending work.

    Implementations must make every mutating method atomic. The in-memory
    version uses an asyncio lock; a future Redis implementation should use
    Redis transactions or Lua scripts while preserving this contract.
    """

    @abstractmethod
    async def submit_task(
        self,
        *,
        task_id: str,
        user_id: str,
        chat_id: str,
    ) -> AdmissionResult:
        """Reserve a chat and either run or queue a new task."""

    @abstractmethod
    async def finish_task(
        self,
        *,
        task_id: str,
        terminal_status: TaskStatus,
    ) -> FinishResult:
        """Release a terminal task and promote newly eligible queued work."""

    @abstractmethod
    async def get_task(self, task_id: str) -> Optional[TaskMetadata]:
        """Return a safe snapshot of active task metadata."""

    @abstractmethod
    async def is_owned_by(self, task_id: str, user_id: str) -> bool:
        """Return whether an active task belongs to the authenticated user."""

    @abstractmethod
    async def running_count(self) -> int:
        """Return the number of tasks currently consuming execution slots."""

    @abstractmethod
    async def queued_count(self) -> int:
        """Return the number of tasks waiting for an execution slot."""
