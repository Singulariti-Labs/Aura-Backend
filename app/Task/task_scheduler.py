"""Application service joining portable coordination with local execution."""

import asyncio
from collections import deque
import logging
from typing import Iterable, Optional

from asyncpg import Pool
from fastapi import WebSocket

from app.DB.Queries.task import create_task, update_task_status
from app.Task.TaskCoordinator import (
    AdmissionResult,
    TaskCoordinator,
    TaskStatus,
    task_coordinator,
)
from app.Task.task_manager import (
    TaskManager,
    TaskRunnerFactory,
    task_manager,
)
from app.api.websocket_utils import send_ws_message


logger = logging.getLogger(__name__)


class TaskScheduler:
    """Admit, queue, execute, and clean up tasks in one server process.

    The scheduler is deliberately backend-neutral. It depends only on the
    ``TaskCoordinator`` contract, so a future Redis coordinator can replace the
    in-memory implementation without changing WebSocket or agent code.
    """

    def __init__(
        self,
        coordinator: TaskCoordinator,
        runtime_manager: TaskManager,
    ):
        self.coordinator = coordinator
        self.runtime_manager = runtime_manager

    async def submit(
        self,
        *,
        task_id: str,
        user_id: str,
        chat_id: str,
        query: str,
        websocket: WebSocket,
        pool: Pool,
        runner_factory: TaskRunnerFactory,
        emit_status: bool = True,
        compression_trigger: Optional[str] = None,
        client_task_id: Optional[str] = None,
        compression_id: Optional[str] = None,
    ) -> AdmissionResult:
        """Reserve a chat, persist the task, and start it when eligible."""

        admission = await self.coordinator.submit_task(
            task_id=task_id,
            user_id=user_id,
            chat_id=chat_id,
        )
        if not admission.accepted:
            return admission

        try:
            self.runtime_manager.register_task(
                task_id,
                websocket=websocket,
                pool=pool,
                user_id=user_id,
                chat_id=chat_id,
                runner_factory=runner_factory,
                emit_status=emit_status,
                compression_trigger=compression_trigger,
                client_task_id=client_task_id,
                compression_id=compression_id,
            )
        except Exception:
            finish_result = await self.coordinator.finish_task(
                task_id=task_id,
                terminal_status=TaskStatus.FAILED,
            )
            await self._start_promoted(finish_result.promoted_task_ids)
            raise

        try:
            await create_task(
                pool=pool,
                task_id=task_id,
                chat_id=chat_id,
                query=query,
                user_id=user_id,
                status=admission.status.value,
            )
        except Exception:
            logger.exception("Failed to persist admitted task %s", task_id)
            self.runtime_manager.remove_task(task_id)
            finish_result = await self.coordinator.finish_task(
                task_id=task_id,
                terminal_status=TaskStatus.FAILED,
            )
            await self._start_promoted(finish_result.promoted_task_ids)
            raise

        if admission.status == TaskStatus.RUNNING:
            try:
                self.runtime_manager.start_task(task_id)
            except Exception:
                logger.exception("Failed to start admitted task %s", task_id)
                await self.finalize(task_id, TaskStatus.FAILED)
                raise
        elif emit_status:
            await self._notify_status(
                websocket=websocket,
                task_id=task_id,
                chat_id=chat_id,
                status="queued",
                message="Task is queued and will start when capacity is available",
            )

        return admission

    async def finalize(
        self,
        task_id: str,
        terminal_status: TaskStatus,
    ) -> bool:
        """Perform idempotent terminal cleanup and start promoted work.

        Cleanup releases chat ownership, decrements running counters, updates
        the database, removes process-local runtime objects, and fills any
        newly available execution slots.
        """

        if not terminal_status.is_terminal:
            raise ValueError("finalize requires a terminal task status")

        state = self.runtime_manager.get_state_or_none(task_id)
        finish_result = await self.coordinator.finish_task(
            task_id=task_id,
            terminal_status=terminal_status,
        )
        if not finish_result.finished:
            return False

        if state is not None and state.dbpool is not None:
            await update_task_status(
                pool=state.dbpool,
                task_id=task_id,
                status=terminal_status.value,
            )

        if state is not None and state.context_ids:
            from app.Context.Store.factory import context_store

            for context_id in tuple(state.context_ids):
                await context_store.delete(context_id)

        self.runtime_manager.remove_task(task_id)
        await self._start_promoted(finish_result.promoted_task_ids)
        return True

    async def cancel(self, task_id: str, user_id: str) -> bool:
        """Cancel an owned queued or running task."""

        metadata = await self.coordinator.get_task(task_id)
        if metadata is None or metadata.user_id != user_id:
            return False

        state = self.runtime_manager.get_state_or_none(task_id)
        if state is None:
            await self.finalize(task_id, TaskStatus.CANCELLED)
            return True

        state.cancelled = True
        if metadata.status == TaskStatus.QUEUED or state.task is None:
            if state.emit_status:
                await self._notify_status(
                    websocket=state.websocket,
                    task_id=task_id,
                    chat_id=metadata.chat_id,
                    status="cancelled",
                    message="Queued task was cancelled",
                )
            elif state.websocket is not None:
                response_task_id = state.client_task_id or task_id
                await send_ws_message(
                    state.websocket,
                    type="compression",
                    task_id=response_task_id,
                    chat_id=metadata.chat_id,
                    compression_id=state.compression_id,
                    payload={
                        "type": "compression",
                        "schema_version": 1,
                        "compression_id": state.compression_id,
                        "task_id": response_task_id,
                        "chat_id": metadata.chat_id,
                        "status": "failed",
                        "trigger": state.compression_trigger or "manual",
                        "trigger_reason": state.compression_trigger or "manual",
                        "message": "Context compression was cancelled",
                        "error": {
                            "code": "COMPRESSION_CANCELLED",
                            "message": "Context compression was cancelled",
                        },
                    },
                )
            await self.finalize(task_id, TaskStatus.CANCELLED)
            return True

        self.runtime_manager.cancel_task(task_id)
        return True

    async def pause(self, task_id: str, user_id: str) -> bool:
        """Pause an owned running task at its next cooperative checkpoint."""

        metadata = await self.coordinator.get_task(task_id)
        if (
            metadata is None
            or metadata.user_id != user_id
            or metadata.status != TaskStatus.RUNNING
        ):
            return False
        return await self.runtime_manager.pause_task(task_id)

    async def resume(self, task_id: str, user_id: str) -> bool:
        """Resume an owned running task."""

        metadata = await self.coordinator.get_task(task_id)
        if (
            metadata is None
            or metadata.user_id != user_id
            or metadata.status != TaskStatus.RUNNING
        ):
            return False
        return await self.runtime_manager.resume_task(task_id)

    async def provide_input(self, task_id: str, user_id: str, data) -> bool:
        """Route client input only when task ownership is valid."""

        if not await self.coordinator.is_owned_by(task_id, user_id):
            return False
        try:
            self.runtime_manager.provide_input(task_id, data)
        except KeyError:
            return False
        return True

    async def disconnect(self, websocket: WebSocket) -> None:
        """Cancel queued/running tasks whose client connection disappeared."""

        task_ids = self.runtime_manager.mark_websocket_closed(websocket)
        running_tasks: list[asyncio.Task] = []

        for task_id in task_ids:
            metadata = await self.coordinator.get_task(task_id)
            state = self.runtime_manager.get_state_or_none(task_id)
            if metadata is None or state is None:
                continue

            if metadata.status == TaskStatus.QUEUED or state.task is None:
                await self.finalize(task_id, TaskStatus.CANCELLED)
                continue

            self.runtime_manager.cancel_task(task_id)
            if state.task is not None:
                running_tasks.append(state.task)

        if running_tasks:
            done, pending = await asyncio.wait(running_tasks, timeout=2)
            for task in done:
                try:
                    task.result()
                except (asyncio.CancelledError, Exception):
                    pass
            if pending:
                logger.warning(
                    "%d disconnected tasks are still stopping",
                    len(pending),
                )

    async def _start_promoted(self, task_ids: Iterable[str]) -> None:
        """Start promoted tasks and safely skip disconnected runtime owners."""

        pending_promotions = deque(task_ids)
        while pending_promotions:
            task_id = pending_promotions.popleft()
            metadata = await self.coordinator.get_task(task_id)
            state = self.runtime_manager.get_state_or_none(task_id)

            if metadata is None:
                continue

            if state is None or state.connection_closed:
                if state is not None and state.dbpool is not None:
                    await update_task_status(
                        pool=state.dbpool,
                        task_id=task_id,
                        status=TaskStatus.CANCELLED.value,
                    )
                self.runtime_manager.remove_task(task_id)
                finish_result = await self.coordinator.finish_task(
                    task_id=task_id,
                    terminal_status=TaskStatus.CANCELLED,
                )
                pending_promotions.extend(finish_result.promoted_task_ids)
                continue

            await update_task_status(
                pool=state.dbpool,
                task_id=task_id,
                status=TaskStatus.RUNNING.value,
            )
            if state.emit_status:
                await self._notify_status(
                    websocket=state.websocket,
                    task_id=task_id,
                    chat_id=metadata.chat_id,
                    status="processing",
                    message="Task left the queue and is starting",
                )
            try:
                self.runtime_manager.start_task(task_id)
            except Exception:
                logger.exception("Failed to start promoted task %s", task_id)
                await update_task_status(
                    pool=state.dbpool,
                    task_id=task_id,
                    status=TaskStatus.FAILED.value,
                )
                self.runtime_manager.remove_task(task_id)
                finish_result = await self.coordinator.finish_task(
                    task_id=task_id,
                    terminal_status=TaskStatus.FAILED,
                )
                pending_promotions.extend(finish_result.promoted_task_ids)

    async def _notify_status(
        self,
        *,
        websocket: Optional[WebSocket],
        task_id: str,
        chat_id: str,
        status: str,
        message: str,
    ) -> None:
        """Best-effort status delivery used by scheduler transitions."""

        if websocket is None:
            return
        try:
            await send_ws_message(
                websocket,
                type="aura_status",
                task_id=task_id,
                chat_id=chat_id,
                payload={
                    "message": message,
                    "status": status,
                },
            )
        except Exception:
            logger.debug(
                "Could not deliver scheduler status for task %s",
                task_id,
                exc_info=True,
            )


task_scheduler = TaskScheduler(task_coordinator, task_manager)
