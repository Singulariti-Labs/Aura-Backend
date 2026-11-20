"""Process-local runtime registry for active agent tasks.

This module stores objects that cannot be persisted in Redis: WebSocket
connections, asyncio Tasks, pause Events, and input Queues. Portable admission
state lives separately in ``app.Task.TaskCoordinator``.
"""

import asyncio
import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional, Set

from asyncpg import Pool
from fastapi import WebSocket


TaskRunnerFactory = Callable[[], Awaitable[None]]

logger = logging.getLogger(__name__)

DEFAULT_CLIENT_TOOL_TIMEOUT_SECONDS = float(
    os.getenv("CLIENT_TOOL_TIMEOUT_SECONDS", "120")
)
FILE_CLIENT_TOOL_TIMEOUT_SECONDS = float(
    os.getenv("CLIENT_FILE_TOOL_TIMEOUT_SECONDS", "300")
)
USER_CLIENT_TOOL_TIMEOUT_SECONDS = float(
    os.getenv("CLIENT_USER_TOOL_TIMEOUT_SECONDS", "900")
)
EXPIRED_TOOL_CALL_TTL_SECONDS = float(
    os.getenv("EXPIRED_TOOL_CALL_TTL_SECONDS", "900")
)

_FILE_TOOLS = {"read_file", "get_file_content", "read_skill", "read_memory"}
_USER_TOOLS = {"ask_user"}


class ClientToolResponseTimeoutError(TimeoutError):
    """Raised to a tool implementation when its client response expires."""

    def __init__(self, tool_name: str, tool_call_id: str, timeout_seconds: float):
        self.tool_name = tool_name
        self.tool_call_id = tool_call_id
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"CLIENT_TOOL_TIMEOUT: {tool_name} did not return a response "
            f"within {timeout_seconds:g} seconds (tool_call_id={tool_call_id})"
        )


@dataclass(slots=True)
class PendingToolCall:
    """One client-side tool request awaiting its correlated response."""

    future: asyncio.Future
    tool_name: str
    timeout_seconds: float
    registered_at: float


class TaskControlState:
    """Runtime controls and client-response queues for one local task."""

    def __init__(
        self,
        websocket: Optional[WebSocket],
        dbpool: Optional[Pool],
        *,
        user_id: Optional[str] = None,
        chat_id: Optional[str] = None,
        runner_factory: Optional[TaskRunnerFactory] = None,
        emit_status: bool = True,
        compression_trigger: Optional[str] = None,
        client_task_id: Optional[str] = None,
        compression_id: Optional[str] = None,
    ):
        self.websocket = websocket
        self.dbpool = dbpool
        self.user_id = user_id
        self.chat_id = chat_id
        self.runner_factory = runner_factory
        self.emit_status = emit_status
        self.compression_trigger = compression_trigger
        self.client_task_id = client_task_id
        self.compression_id = compression_id
        self.task: Optional[asyncio.Task] = None
        self.connection_closed = False
        self.cancelled = False
        self._seq = 0
        self.paused = asyncio.Event()
        self.input_queue: asyncio.Queue = asyncio.Queue()
        self.pending_tool_calls: Dict[str, PendingToolCall] = {}
        self.expired_tool_call_ids: Dict[str, float] = {}
        self.context_ids: Set[str] = set()
        self.paused.set()

    def get_next_seq(self) -> int:
        """Return the next event sequence number for this task."""

        self._seq += 1
        return self._seq

    def register_context(self, context_id: str) -> None:
        self.context_ids.add(context_id)

    def _default_timeout_for_tool(self, tool_name: str) -> float:
        if tool_name in _FILE_TOOLS:
            return FILE_CLIENT_TOOL_TIMEOUT_SECONDS
        if tool_name in _USER_TOOLS:
            return USER_CLIENT_TOOL_TIMEOUT_SECONDS
        return DEFAULT_CLIENT_TOOL_TIMEOUT_SECONDS

    def _prune_expired_tool_calls(self) -> None:
        now = asyncio.get_running_loop().time()
        stale_ids = [
            tool_call_id
            for tool_call_id, expires_at in self.expired_tool_call_ids.items()
            if expires_at <= now
        ]
        for tool_call_id in stale_ids:
            self.expired_tool_call_ids.pop(tool_call_id, None)

    def register_tool_call(
        self,
        tool_call_id: str,
        *,
        tool_name: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> PendingToolCall:
        """Register a response Future before the request is sent to the client."""

        if not tool_call_id:
            raise ValueError("tool_call_id is required")

        self._prune_expired_tool_calls()
        normalized_id = str(tool_call_id)
        existing = self.pending_tool_calls.get(normalized_id)
        if existing is not None:
            return existing
        if normalized_id in self.expired_tool_call_ids:
            raise ValueError(
                f"tool_call_id {normalized_id!r} has expired and cannot be reused"
            )

        normalized_tool_name = str(tool_name or "client_tool")
        effective_timeout = (
            float(timeout_seconds)
            if timeout_seconds is not None
            else self._default_timeout_for_tool(normalized_tool_name)
        )
        if effective_timeout <= 0:
            raise ValueError("timeout_seconds must be greater than zero")

        loop = asyncio.get_running_loop()
        pending = PendingToolCall(
            future=loop.create_future(),
            tool_name=normalized_tool_name,
            timeout_seconds=effective_timeout,
            registered_at=loop.time(),
        )
        self.pending_tool_calls[normalized_id] = pending
        return pending

    def expire_tool_call(self, tool_call_id: str, *, reason: str) -> bool:
        """Remove a pending call and retain a short tombstone for late replies."""

        normalized_id = str(tool_call_id)
        pending = self.pending_tool_calls.pop(normalized_id, None)
        if pending is None:
            return False
        if not pending.future.done():
            pending.future.cancel()
        self.expired_tool_call_ids[normalized_id] = (
            asyncio.get_running_loop().time() + EXPIRED_TOOL_CALL_TTL_SECONDS
        )
        logger.debug(
            "Expired client tool call id=%s tool=%s reason=%s",
            normalized_id,
            pending.tool_name,
            reason,
        )
        return True

    def cancel_pending_tool_calls(self, *, reason: str) -> None:
        """Cancel and clean every waiter owned by this task."""

        for tool_call_id in tuple(self.pending_tool_calls):
            self.expire_tool_call(tool_call_id, reason=reason)

    def route_input(self, data: Any) -> str:
        """Route responses by call ID and discard unknown or expired replies."""

        payload = data.get("payload", {}) if isinstance(data, dict) else {}
        tool_call_id = payload.get("tool_call_id")
        message_type = data.get("type") if isinstance(data, dict) else None
        if message_type == "client_tool_response":
            if not tool_call_id:
                logger.warning("Discarding client tool response without tool_call_id")
                return "invalid"

            self._prune_expired_tool_calls()
            normalized_id = str(tool_call_id)
            pending = self.pending_tool_calls.get(normalized_id)
            if pending is not None and not pending.future.done():
                pending.future.set_result(data)
                return "delivered"

            response_status = (
                "late" if normalized_id in self.expired_tool_call_ids else "unknown"
            )
            logger.info(
                "Discarding %s client tool response id=%s tool=%s",
                response_status,
                normalized_id,
                payload.get("tool"),
            )
            return response_status

        self.input_queue.put_nowait(data)
        return "general_input"

    async def wait_for_tool_response(
        self,
        tool_call_id: str,
        *,
        timeout_seconds: Optional[float] = None,
    ) -> Any:
        """Wait for the response belonging to one exact client tool call."""

        if not tool_call_id:
            raise ValueError("tool_call_id is required")

        normalized_id = str(tool_call_id)
        pending = self.pending_tool_calls.get(normalized_id)
        if pending is None:
            pending = self.register_tool_call(
                normalized_id,
                timeout_seconds=timeout_seconds,
            )
        configured_timeout = (
            float(timeout_seconds)
            if timeout_seconds is not None
            else pending.timeout_seconds
        )
        elapsed = asyncio.get_running_loop().time() - pending.registered_at
        remaining_timeout = max(0.0, configured_timeout - elapsed)
        try:
            return await asyncio.wait_for(
                asyncio.shield(pending.future),
                timeout=remaining_timeout,
            )
        except asyncio.TimeoutError as exc:
            self.expire_tool_call(normalized_id, reason="timeout")
            raise ClientToolResponseTimeoutError(
                pending.tool_name,
                normalized_id,
                configured_timeout,
            ) from exc
        except asyncio.CancelledError:
            self.expire_tool_call(normalized_id, reason="waiter_cancelled")
            raise
        finally:
            if self.pending_tool_calls.get(normalized_id) is pending:
                self.pending_tool_calls.pop(normalized_id, None)


class TaskManager:
    """Maintain local runtime objects without owning admission policy."""

    def __init__(self):
        self.tasks: Dict[str, TaskControlState] = {}
        self._task_ids_by_websocket: Dict[int, Set[str]] = defaultdict(set)

    def register_task(
        self,
        task_id: str,
        *,
        websocket: Optional[WebSocket],
        pool: Optional[Pool],
        user_id: Optional[str],
        chat_id: Optional[str],
        runner_factory: Optional[TaskRunnerFactory],
        emit_status: bool = True,
        compression_trigger: Optional[str] = None,
        client_task_id: Optional[str] = None,
        compression_id: Optional[str] = None,
    ) -> TaskControlState:
        """Register queued or running local state for a newly admitted task."""

        if task_id in self.tasks:
            raise ValueError(f"Task ID {task_id} is already registered")

        state = TaskControlState(
            websocket=websocket,
            dbpool=pool,
            user_id=user_id,
            chat_id=chat_id,
            runner_factory=runner_factory,
            emit_status=emit_status,
            compression_trigger=compression_trigger,
            client_task_id=client_task_id,
            compression_id=compression_id,
        )
        self.tasks[task_id] = state
        if websocket is not None:
            self._task_ids_by_websocket[id(websocket)].add(task_id)
        return state

    def create_task(
        self,
        task_id: str,
        websocket: Optional[WebSocket],
        pool: Optional[Pool],
    ) -> None:
        """Backward-compatible registration used by existing unit tests."""

        self.register_task(
            task_id,
            websocket=websocket,
            pool=pool,
            user_id=None,
            chat_id=None,
            runner_factory=None,
            emit_status=True,
        )

    def start_task(self, task_id: str) -> asyncio.Task:
        """Create the asyncio Task for a registered, promoted task."""

        state = self.get_state(task_id)
        if state.connection_closed:
            raise RuntimeError(f"Cannot start task {task_id}; connection is closed")
        if state.task is not None and not state.task.done():
            raise RuntimeError(f"Task {task_id} is already running")
        if state.runner_factory is None:
            raise RuntimeError(f"Task {task_id} has no runner factory")

        task = asyncio.create_task(
            state.runner_factory(),
            name=f"agent-task:{task_id}",
        )
        state.task = task
        return task

    def set_task(self, task_id: str, task: asyncio.Task) -> None:
        """Attach an already-created asyncio Task for compatibility."""

        self.get_state(task_id).task = task

    def get_state(self, task_id: str) -> TaskControlState:
        """Return local runtime state or raise a descriptive KeyError."""

        state = self.tasks.get(task_id)
        if state is None:
            raise KeyError(f"Task ID {task_id} not found in TaskManager.")
        return state

    def get_state_or_none(self, task_id: str) -> Optional[TaskControlState]:
        """Return local runtime state when present."""

        return self.tasks.get(task_id)

    def cancel_task(self, task_id: str) -> bool:
        """Mark a task cancelled and cancel its coroutine when running."""

        state = self.tasks.get(task_id)
        if state is None:
            return False
        state.cancelled = True
        state.paused.set()
        state.cancel_pending_tool_calls(reason="task_cancelled")
        if state.task is not None and not state.task.done():
            state.task.cancel()
        return True

    async def pause_task(self, task_id: str) -> bool:
        """Pause a task at its next cooperative pause checkpoint."""

        state = self.tasks.get(task_id)
        if state is None:
            return False
        state.paused.clear()
        return True

    async def resume_task(self, task_id: str) -> bool:
        """Resume a cooperatively paused task."""

        state = self.tasks.get(task_id)
        if state is None:
            return False
        state.paused.set()
        return True

    async def wait_if_paused(self, task_id: str) -> None:
        """Block at a cooperative checkpoint while a task is paused."""

        await self.get_state(task_id).paused.wait()

    def register_context(self, task_id: str, context_id: str) -> None:
        self.get_state(task_id).register_context(context_id)

    def provide_input(self, task_id: str, data: Any) -> None:
        """Deliver user input or a client tool response to one task."""

        self.get_state(task_id).route_input(data)

    async def wait_for_input(self, task_id: str) -> Any:
        """Wait for the next legacy user or client-tool response."""

        return await self.get_state(task_id).input_queue.get()

    def register_tool_call(
        self,
        task_id: str,
        tool_call_id: str,
        *,
        tool_name: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> PendingToolCall:
        """Register one pending client call on its owning task."""

        return self.get_state(task_id).register_tool_call(
            tool_call_id,
            tool_name=tool_name,
            timeout_seconds=timeout_seconds,
        )

    def expire_tool_call(
        self,
        task_id: str,
        tool_call_id: str,
        *,
        reason: str,
    ) -> bool:
        """Expire a client call after a send failure or explicit cancellation."""

        state = self.get_state_or_none(task_id)
        if state is None:
            return False
        return state.expire_tool_call(tool_call_id, reason=reason)

    async def wait_for_tool_response(
        self,
        task_id: str,
        tool_call_id: str,
        *,
        timeout_seconds: Optional[float] = None,
    ) -> Any:
        """Wait for one client tool response correlated by tool-call ID."""

        return await self.get_state(task_id).wait_for_tool_response(
            tool_call_id,
            timeout_seconds=timeout_seconds,
        )

    def mark_websocket_closed(self, websocket: WebSocket) -> tuple[str, ...]:
        """Mark all task states owned by a disconnected socket."""

        task_ids = tuple(self._task_ids_by_websocket.get(id(websocket), set()))
        for task_id in task_ids:
            state = self.tasks.get(task_id)
            if state is not None:
                state.connection_closed = True
        return task_ids

    def task_ids_for_websocket(self, websocket: WebSocket) -> tuple[str, ...]:
        """Return a snapshot of task IDs attached to a WebSocket."""

        return tuple(self._task_ids_by_websocket.get(id(websocket), set()))

    def remove_task(self, task_id: str) -> Optional[TaskControlState]:
        """Remove and return local runtime state for a terminal task."""

        state = self.tasks.pop(task_id, None)
        if state is None:
            return None

        state.cancel_pending_tool_calls(reason="task_removed")

        if state.websocket is not None:
            websocket_key = id(state.websocket)
            task_ids = self._task_ids_by_websocket.get(websocket_key)
            if task_ids is not None:
                task_ids.discard(task_id)
                if not task_ids:
                    self._task_ids_by_websocket.pop(websocket_key, None)
        return state


task_manager = TaskManager()
