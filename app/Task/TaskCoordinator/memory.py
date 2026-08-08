"""Single-process task coordinator backed by in-memory collections."""

import asyncio
from collections import OrderedDict, defaultdict
from dataclasses import replace
from typing import Dict, Optional, Set, Tuple

from .base import TaskCoordinator
from .config import TaskCoordinatorSettings
from .models import (
    AdmissionResult,
    FinishResult,
    RejectionReason,
    TaskMetadata,
    TaskStatus,
)


class InMemoryTaskCoordinator(TaskCoordinator):
    """Coordinate tasks safely inside one Python process.

    The class deliberately mirrors operations that can later be implemented
    atomically in Redis. It supports multiple users, multiple parallel chats
    per user, one active task per chat, bounded FIFO queueing, and fair
    promotion that skips users who already consume their running-task limit.
    """

    def __init__(self, settings: TaskCoordinatorSettings):
        self.settings = settings
        self._lock = asyncio.Lock()
        self._tasks: Dict[str, TaskMetadata] = {}
        self._active_chats: Dict[Tuple[str, str], str] = {}
        self._running_by_user: Dict[str, Set[str]] = defaultdict(set)
        self._queued_task_ids: "OrderedDict[str, None]" = OrderedDict()

    async def submit_task(
        self,
        *,
        task_id: str,
        user_id: str,
        chat_id: str,
    ) -> AdmissionResult:
        """Atomically reserve the chat and admit or queue the task."""

        task_id = task_id.strip()
        user_id = user_id.strip()
        chat_id = chat_id.strip()
        if not task_id or not user_id or not chat_id:
            return AdmissionResult(
                accepted=False,
                reason=RejectionReason.INVALID_REQUEST,
            )

        async with self._lock:
            if task_id in self._tasks:
                return AdmissionResult(
                    accepted=False,
                    reason=RejectionReason.DUPLICATE_TASK_ID,
                    conflicting_task_id=task_id,
                )

            chat_key = (user_id, chat_id)
            existing_task_id = self._active_chats.get(chat_key)
            if existing_task_id is not None:
                return AdmissionResult(
                    accepted=False,
                    reason=RejectionReason.CHAT_ALREADY_RUNNING,
                    conflicting_task_id=existing_task_id,
                )

            can_start = self._can_start_for_user(user_id)
            if not can_start and (
                len(self._queued_task_ids)
                >= self.settings.max_queued_per_instance
            ):
                return AdmissionResult(
                    accepted=False,
                    reason=RejectionReason.QUEUE_FULL,
                )

            status = TaskStatus.RUNNING if can_start else TaskStatus.QUEUED
            metadata = TaskMetadata(
                task_id=task_id,
                user_id=user_id,
                chat_id=chat_id,
                status=status,
            )
            self._tasks[task_id] = metadata
            self._active_chats[chat_key] = task_id

            if status == TaskStatus.RUNNING:
                self._running_by_user[user_id].add(task_id)
            else:
                self._queued_task_ids[task_id] = None

            return AdmissionResult(accepted=True, status=status)

    async def finish_task(
        self,
        *,
        task_id: str,
        terminal_status: TaskStatus,
    ) -> FinishResult:
        """Remove an active task and promote eligible queued tasks."""

        if not terminal_status.is_terminal:
            raise ValueError("finish_task requires a terminal task status")

        async with self._lock:
            metadata = self._tasks.pop(task_id, None)
            if metadata is None:
                return FinishResult(finished=False)

            self._queued_task_ids.pop(task_id, None)
            running_for_user = self._running_by_user.get(metadata.user_id)
            if running_for_user is not None:
                running_for_user.discard(task_id)
                if not running_for_user:
                    self._running_by_user.pop(metadata.user_id, None)

            chat_key = (metadata.user_id, metadata.chat_id)
            if self._active_chats.get(chat_key) == task_id:
                self._active_chats.pop(chat_key, None)

            promoted = self._promote_eligible_tasks()
            return FinishResult(
                finished=True,
                promoted_task_ids=tuple(promoted),
            )

    async def get_task(self, task_id: str) -> Optional[TaskMetadata]:
        """Return an immutable snapshot of active task metadata."""

        async with self._lock:
            metadata = self._tasks.get(task_id)
            return replace(metadata) if metadata is not None else None

    async def is_owned_by(self, task_id: str, user_id: str) -> bool:
        """Validate task ownership without exposing coordinator internals."""

        async with self._lock:
            metadata = self._tasks.get(task_id)
            return metadata is not None and metadata.user_id == user_id

    async def running_count(self) -> int:
        """Return the current number of running tasks."""

        async with self._lock:
            return self._total_running()

    async def queued_count(self) -> int:
        """Return the current number of queued tasks."""

        async with self._lock:
            return len(self._queued_task_ids)

    def _total_running(self) -> int:
        return sum(len(task_ids) for task_ids in self._running_by_user.values())

    def _can_start_for_user(self, user_id: str) -> bool:
        return (
            self._total_running() < self.settings.max_running_per_instance
            and len(self._running_by_user.get(user_id, set()))
            < self.settings.max_running_per_user
        )

    def _promote_eligible_tasks(self) -> list[str]:
        """Promote queued tasks without head-of-line blocking.

        A task belonging to a user at their limit remains in FIFO position, but
        does not prevent an eligible task from another user from using an idle
        instance slot.
        """

        promoted: list[str] = []
        while self._total_running() < self.settings.max_running_per_instance:
            selected_task_id: Optional[str] = None
            for queued_task_id in self._queued_task_ids:
                metadata = self._tasks[queued_task_id]
                if self._can_start_for_user(metadata.user_id):
                    selected_task_id = queued_task_id
                    break

            if selected_task_id is None:
                break

            self._queued_task_ids.pop(selected_task_id, None)
            metadata = self._tasks[selected_task_id]
            self._tasks[selected_task_id] = replace(
                metadata,
                status=TaskStatus.RUNNING,
            )
            self._running_by_user[metadata.user_id].add(selected_task_id)
            promoted.append(selected_task_id)

        return promoted
