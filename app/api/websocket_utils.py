"""Helpers for standardized, concurrency-safe WebSocket messages."""

import asyncio
import uuid
from typing import Optional

from fastapi import WebSocket

from app.Types.agent_types import WS_MESSAGE_TYPE


async def send_ws_message(
    websocket: WebSocket,
    *,
    type: WS_MESSAGE_TYPE,
    task_id: Optional[str],
    chat_id: Optional[str],
    payload: Optional[dict] = None,
) -> Optional[str]:
    """Send one atomic task-scoped message over a shared WebSocket.

    Several agent tasks may write to the same user connection concurrently.
    The connection-level lock prevents overlapping ASGI send operations while
    ``task_id`` and ``chat_id`` let the client route interleaved messages to the
    correct conversation.
    """

    message_payload = dict(payload or {})
    registered_tool_call_id: Optional[str] = None
    if type == "client_tool_request":
        registered_tool_call_id = str(
            message_payload.get("tool_call_id") or uuid.uuid4()
        )
        message_payload["tool_call_id"] = registered_tool_call_id

        # Register before the first await so an immediate client response
        # always has the correct Future to resolve.
        from app.Task.task_manager import task_manager

        tool_name = str(message_payload.get("tool") or "client_tool")
        timeout_seconds = None
        if tool_name == "execute_command":
            command_timeout = message_payload.get("input", {}).get("timeout")
            if isinstance(command_timeout, (int, float)) and command_timeout > 0:
                timeout_seconds = max(120.0, float(command_timeout) + 30.0)
        task_manager.register_tool_call(
            str(task_id),
            registered_tool_call_id,
            tool_name=tool_name,
            timeout_seconds=timeout_seconds,
        )

    message = {
        "type": type,
        "task_id": task_id,
        "chat_id": chat_id,
        "payload": message_payload,
    }

    send_lock = getattr(websocket.state, "send_lock", None)
    if send_lock is None:
        # Tests and non-standard callers may bypass ConnectionManager.connect.
        # Assignment has no await point, so creation is atomic on this loop.
        send_lock = asyncio.Lock()
        websocket.state.send_lock = send_lock

    try:
        async with send_lock:
            await websocket.send_json(message)
    except BaseException:
        if registered_tool_call_id is not None:
            from app.Task.task_manager import task_manager

            task_manager.expire_tool_call(
                str(task_id),
                registered_tool_call_id,
                reason="websocket_send_failed",
            )
        raise

    return registered_tool_call_id
