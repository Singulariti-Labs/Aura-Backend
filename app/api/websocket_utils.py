"""Helpers for standardized, concurrency-safe WebSocket messages."""

import asyncio
import uuid
from typing import Optional

from fastapi import WebSocket

from app.Types.agent_types import WS_MESSAGE_TYPE


COMPRESSION_ID_PREFIX = "compression_"


def normalize_compression_id(value: object) -> str:
    """Validate and canonicalize a client-supplied compression operation ID."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError("compression_id must be a non-empty string when provided")
    candidate = value.strip()
    if not candidate.startswith(COMPRESSION_ID_PREFIX):
        raise ValueError("compression_id must use the format compression_<uuid>")
    try:
        parsed = uuid.UUID(candidate[len(COMPRESSION_ID_PREFIX):])
    except (ValueError, AttributeError) as exc:
        raise ValueError(
            "compression_id must use the format compression_<uuid>"
        ) from exc
    return f"{COMPRESSION_ID_PREFIX}{parsed}"


async def send_ws_message(
    websocket: WebSocket,
    *,
    type: WS_MESSAGE_TYPE,
    task_id: Optional[str],
    chat_id: Optional[str],
    payload: Optional[dict] = None,
    compression_id: Optional[str] = None,
) -> Optional[str]:
    """Send one atomic task-scoped message over a shared WebSocket.

    Several agent tasks may write to the same user connection concurrently.
    The connection-level lock prevents overlapping ASGI send operations while
    ``task_id`` and ``chat_id`` let the client route interleaved messages to the
    correct conversation. ``compression_id`` optionally identifies one
    compression lifecycle without changing the task identity.
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
    if compression_id is not None:
        message["compression_id"] = compression_id

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
