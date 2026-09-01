"""WebSocket endpoint for concurrent, task-scoped agent execution."""

import asyncio
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from app.Agents.agent import Agent
from app.DB.Queries.user import get_user_by_auth0_id
from app.DB.pool import get_pool
from app.RateLimit.rate_limit import check_rate_limit_for_request
from app.Task.TaskCoordinator import RejectionReason, TaskStatus
from app.Task.task_scheduler import task_scheduler
from app.Types.agent_types import (
    AuraConfig,
    ConsciousFiles,
    LLMConfig,
    OpenApplications,
    SystemInfo,
)
from app.LLM.model_bridge.common import normalize_history
from app.api.auth_utils import token_verifier
from app.api.connection_manager import ConnectionManager
from app.api.llm_config_utils import resolve_llm_config
from app.api.websocket_utils import normalize_compression_id, send_ws_message


logger = logging.getLogger(__name__)
ws_router = APIRouter()
manager = ConnectionManager()
llm_config = LLMConfig(provider="anthropic", model_name="claude-opus-4-8")


def _truncate_screenshot_for_log(value, preview_length: int = 24):
    """Return a log-safe payload copy with large sensitive fields shortened."""

    if isinstance(value, dict):
        return {
            key: (
                _format_screenshot_preview(item, preview_length)
                if key == "screenshot"
                else _format_memory_context_preview(item)
                if key == "memory_context"
                else _truncate_screenshot_for_log(item, preview_length)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_truncate_screenshot_for_log(item, preview_length) for item in value]
    return value


def _format_screenshot_preview(value, preview_length: int = 24):
    """Shorten base64 screenshot values before logging."""

    if isinstance(value, str):
        return (
            f"{value[:preview_length]}...."
            if len(value) > preview_length
            else value
        )
    if isinstance(value, list):
        return [_format_screenshot_preview(item, preview_length) for item in value]
    if isinstance(value, dict):
        return {
            key: _format_screenshot_preview(item, preview_length)
            for key, item in value.items()
        }
    return value


def _format_memory_context_preview(value, preview_length: int = 160):
    """Avoid copying the complete historical-memory block into server logs."""

    if not isinstance(value, str):
        return value
    if len(value) <= preview_length:
        return value
    return f"{value[:preview_length]}...[memory_context truncated]"


async def _send_error(
    websocket: WebSocket,
    *,
    task_id: Optional[str],
    chat_id: Optional[str],
    error_code: str,
    message: str,
) -> None:
    """Send a standardized task-scoped error message."""

    await send_ws_message(
        websocket,
        type="error_message",
        task_id=task_id,
        chat_id=chat_id,
        payload={
            "error_code": error_code,
            "message": message,
        },
    )


async def _send_compression_failure(
    websocket: WebSocket,
    *,
    task_id: Optional[str],
    chat_id: Optional[str],
    compression_id: Optional[str],
    error_code: str,
    message: str,
    trigger_reason: str = "manual",
) -> None:
    """Send the sole terminal response for a failed manual compression."""

    await send_ws_message(
        websocket,
        type="compression",
        task_id=task_id,
        chat_id=chat_id,
        compression_id=compression_id,
        payload={
            "type": "compression",
            "schema_version": 1,
            "compression_id": compression_id,
            "task_id": task_id,
            "chat_id": chat_id,
            "status": "failed",
            "trigger": trigger_reason,
            "trigger_reason": trigger_reason,
            "message": message,
            "error": {
                "code": error_code,
                "message": message,
            },
        },
    )


def _admission_error(reason: RejectionReason) -> tuple[str, str]:
    """Map coordinator rejection reasons to stable client errors."""

    errors = {
        RejectionReason.DUPLICATE_TASK_ID: (
            "DUPLICATE_TASK_ID",
            "A task with this task_id is already active",
        ),
        RejectionReason.CHAT_ALREADY_RUNNING: (
            "CHAT_ALREADY_RUNNING",
            "This chat already has a queued or running task",
        ),
        RejectionReason.QUEUE_FULL: (
            "TASK_QUEUE_FULL",
            "The server task queue is full; please retry shortly",
        ),
        RejectionReason.INVALID_REQUEST: (
            "INVALID_TASK_REQUEST",
            "task_id, user_id, and chat_id are required",
        ),
    }
    return errors[reason]


@ws_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Authenticate one client and multiplex its independently tracked tasks."""

    client_host = websocket.client.host if websocket.client else "unknown"
    client_port = websocket.client.port if websocket.client else "unknown"
    logger.info(
        "WebSocket connection attempt from %s:%s",
        client_host,
        client_port,
    )

    await manager.connect(websocket)
    logger.info("WebSocket connected for %s:%s", client_host, client_port)

    try:
        token = websocket.query_params.get("token")
        if not token:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        try:
            user_payload = token_verifier.verify(token)
        except Exception:
            logger.warning(
                "WebSocket token verification failed for %s:%s",
                client_host,
                client_port,
                exc_info=True,
            )
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        pool = await get_pool()
        rate_limit_loop = asyncio.get_running_loop()
        auth0_id = user_payload.get("sub")
        user = await get_user_by_auth0_id(pool, auth0_id)
        if not user:
            logger.warning("Authenticated Auth0 user is absent from the database")
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        user_id = str(user["id"])
        logger.info(
            "WebSocket user authenticated: email=%s user_id=%s",
            user.get("email", "unknown"),
            user_id,
        )

        async def handle_query(
            message: dict,
            task_id: str,
            runtime_task_id: str,
            chat_id: str,
            query: str,
            compression_id: Optional[str] = None,
        ) -> None:
            """Execute one admitted request and always perform terminal cleanup."""

            terminal_status = TaskStatus.FAILED
            compression_only = message.get("type") == "compression"
            try:
                payload = message["payload"]
                logger.debug(
                    "Starting task %s runtime_task_id=%s compression_id=%s payload=%s",
                    task_id,
                    runtime_task_id,
                    compression_id,
                    _truncate_screenshot_for_log(payload),
                )

                raw_aura_config_data = payload.get("aura_config") or {}
                aura_config_data = (
                    raw_aura_config_data
                    if isinstance(raw_aura_config_data, dict)
                    else {}
                )
                user_timezone = aura_config_data.get("timezone", "Asia/Kolkata")

                rate_limit_decision = await check_rate_limit_for_request(
                    pool=pool,
                    user_id=user_id,
                    timezone_name=user_timezone,
                )
                if not rate_limit_decision.allowed:
                    rate_limit_message = (
                        f"You've reached the {rate_limit_decision.plan_code} "
                        "plan's 12-hour usage limit. "
                        "Your usage resets at "
                        f"{rate_limit_decision.reset_at_display}."
                    )
                    if compression_only:
                        await _send_compression_failure(
                            websocket,
                            task_id=task_id,
                            chat_id=chat_id,
                            compression_id=compression_id,
                            error_code="RATE_LIMIT_EXCEEDED",
                            message=rate_limit_message,
                            trigger_reason=str(payload.get("trigger") or "manual"),
                        )
                    else:
                        await send_ws_message(
                            websocket,
                            type="aura_message",
                            task_id=task_id,
                            chat_id=chat_id,
                            payload={
                                "content": {
                                    "role": "assistant",
                                    "message": rate_limit_message,
                                },
                                "coming_from": "rate_limit/server",
                                "rate_limit": {
                                    "plan_code": rate_limit_decision.plan_code,
                                    "spent_usd": str(rate_limit_decision.spent_usd),
                                    "limit_usd": str(rate_limit_decision.limit_usd),
                                    "window_hours": 12,
                                    "reset_at": rate_limit_decision.reset_at.isoformat(),
                                },
                            },
                        )
                    return

                sys_info_data = payload.get("system_info") or {}
                system_info = SystemInfo(
                    os=sys_info_data.get("os", "windows"),
                    version=sys_info_data.get("os_version", "11"),
                    workspace=sys_info_data.get("workspace", ""),
                    cwd=sys_info_data.get("cwd", ""),
                )

                current_llm_config, using_task_config = resolve_llm_config(
                    api_config=payload.get("api_config"),
                    default_config=llm_config,
                )
                logger.info(
                    "Task %s using %s LLM config: provider=%s model=%s",
                    task_id,
                    "request" if using_task_config else "default",
                    current_llm_config.provider,
                    current_llm_config.model_name,
                )

                aura_config = AuraConfig(
                    conscious_files=(
                        ConsciousFiles(**aura_config_data["conscious_files"])
                        if aura_config_data.get("conscious_files")
                        else None
                    ),
                    open_apps=(
                        OpenApplications(**aura_config_data["open_apps"])
                        if aura_config_data.get("open_apps")
                        else None
                    ),
                    timezone=user_timezone,
                    compression=aura_config_data.get("compression", True),
                    boot_me=aura_config_data.get("boot_me", False),
                    local_skills=aura_config_data.get("local_skills"),
                )

                if message.get("type") == "compression":
                    payload["_force_preflight_compression"] = True

                agent = Agent(
                    llm=current_llm_config,
                    query=query,
                    payload=payload,
                    system_info=system_info,
                    task_id=task_id,
                    runtime_task_id=runtime_task_id,
                    compression_id=compression_id,
                    chat_id=chat_id,
                    pool=pool,
                    user_id=user_id,
                    rate_limit_loop=rate_limit_loop,
                    aura_config=aura_config,
                    history=payload.get("messages", []),
                    attached_files=payload.get("attached_files", []),
                    attached_images=payload.get("attached_images", []),
                    screenshot=payload.get("screenshot"),
                )

                if not compression_only:
                    await send_ws_message(
                        websocket,
                        type="aura_status",
                        task_id=task_id,
                        chat_id=chat_id,
                        payload={
                            "query": query,
                            "message": "Agent is processing the request",
                            "status": "processing",
                        },
                    )

                await agent.invoke()
                terminal_status = TaskStatus.COMPLETED

            except asyncio.CancelledError:
                terminal_status = TaskStatus.CANCELLED
                try:
                    if compression_only:
                        await _send_compression_failure(
                            websocket,
                            task_id=task_id,
                            chat_id=chat_id,
                            compression_id=compression_id,
                            error_code="COMPRESSION_CANCELLED",
                            message="Context compression was cancelled",
                            trigger_reason=str(
                                message.get("payload", {}).get("trigger") or "manual"
                            ),
                        )
                    else:
                        await send_ws_message(
                            websocket,
                            type="aura_status",
                            task_id=task_id,
                            chat_id=chat_id,
                            payload={
                                "query": query,
                                "message": "Task was cancelled",
                                "status": "cancelled",
                            },
                        )
                except Exception:
                    logger.debug(
                        "Client disconnected before cancellation status delivery",
                        exc_info=True,
                    )
            except Exception as exc:
                logger.exception("Task %s failed", task_id)
                try:
                    if compression_only:
                        await _send_compression_failure(
                            websocket,
                            task_id=task_id,
                            chat_id=chat_id,
                            compression_id=compression_id,
                            error_code="COMPRESSION_FAILED",
                            message=str(exc),
                            trigger_reason=str(
                                message.get("payload", {}).get("trigger") or "manual"
                            ),
                        )
                    else:
                        await _send_error(
                            websocket,
                            task_id=task_id,
                            chat_id=chat_id,
                            error_code="SYSTEM_ERROR",
                            message=f"ERROR: {exc}",
                        )
                except Exception:
                    logger.debug(
                        "Client disconnected before task error delivery",
                        exc_info=True,
                    )
            finally:
                await task_scheduler.finalize(runtime_task_id, terminal_status)

        while True:
            try:
                message = await websocket.receive_json()
            except ValueError:
                await _send_error(
                    websocket,
                    task_id=None,
                    chat_id=None,
                    error_code="INVALID_JSON",
                    message="Invalid JSON message",
                )
                continue

            if not isinstance(message, dict):
                await _send_error(
                    websocket,
                    task_id=None,
                    chat_id=None,
                    error_code="INVALID_MESSAGE",
                    message="WebSocket messages must be JSON objects",
                )
                continue

            msg_type = message.get("type")
            task_id = message.get("task_id")
            chat_id = message.get("chat_id")
            compression_id: Optional[str] = None
            compression_id_was_supplied = False

            if msg_type == "compression":
                raw_compression_id = message.get("compression_id")
                if raw_compression_id is None:
                    compression_id = f"compression_{uuid.uuid4()}"
                else:
                    try:
                        compression_id = normalize_compression_id(
                            raw_compression_id
                        )
                    except ValueError as exc:
                        await _send_compression_failure(
                            websocket,
                            task_id=(
                                task_id if isinstance(task_id, str) else None
                            ),
                            chat_id=chat_id,
                            compression_id=None,
                            error_code="INVALID_COMPRESSION_ID",
                            message=str(exc),
                        )
                        continue
                    compression_id_was_supplied = True

                compression_payload = message.get("payload")
                if not isinstance(compression_payload, dict):
                    await _send_compression_failure(
                        websocket,
                        task_id=task_id if isinstance(task_id, str) else None,
                        chat_id=chat_id,
                        compression_id=compression_id,
                        error_code="PAYLOAD_NOT_FOUND",
                        message="A compression payload is required",
                    )
                    continue

                compression_trigger = compression_payload.get("trigger")
                logger.debug(
                    "Compression request task_id=%s compression_id=%s trigger=%r",
                    task_id,
                    compression_id,
                    compression_trigger,
                )

                compression_range = compression_payload.get("range")
                if compression_range is not None and not isinstance(
                    compression_range, dict
                ):
                    await _send_compression_failure(
                        websocket,
                        task_id=task_id if isinstance(task_id, str) else None,
                        chat_id=chat_id,
                        compression_id=compression_id,
                        error_code="INVALID_COMPRESSION_RANGE",
                        message="payload.range must be an object",
                        trigger_reason=str(compression_trigger or "manual"),
                    )
                    continue

                compression_messages = compression_payload.get("messages")
                if not isinstance(compression_messages, list):
                    await _send_compression_failure(
                        websocket,
                        task_id=task_id if isinstance(task_id, str) else None,
                        chat_id=chat_id,
                        compression_id=compression_id,
                        error_code="INVALID_CHAT_HISTORY",
                        message="payload.messages must be an array",
                        trigger_reason=str(compression_trigger or "manual"),
                    )
                    continue
                try:
                    normalized_compression_messages = normalize_history(
                        compression_messages
                    )
                except ValueError as exc:
                    await _send_compression_failure(
                        websocket,
                        task_id=task_id if isinstance(task_id, str) else None,
                        chat_id=chat_id,
                        compression_id=compression_id,
                        error_code="INVALID_CHAT_HISTORY",
                        message=str(exc),
                        trigger_reason=str(compression_trigger or "manual"),
                    )
                    continue
                compression_payload["messages"] = normalized_compression_messages

            if msg_type in ("task_request", "boot_me", "compression"):
                task_id = str(task_id or uuid.uuid4())
                runtime_task_id = (
                    compression_id
                    if msg_type == "compression" and compression_id_was_supplied
                    else task_id
                )
                if not isinstance(chat_id, str) or not chat_id.strip():
                    if msg_type == "compression":
                        await _send_compression_failure(
                            websocket,
                            task_id=task_id,
                            chat_id=chat_id,
                            compression_id=compression_id,
                            error_code="MISSING_CHAT_ID",
                            message="A non-empty chat_id is required",
                            trigger_reason=str(
                                message.get("payload", {}).get("trigger") or "manual"
                            ),
                        )
                    else:
                        await _send_error(
                            websocket,
                            task_id=task_id,
                            chat_id=chat_id,
                            error_code="MISSING_CHAT_ID",
                            message="A non-empty chat_id is required",
                        )
                    continue

                payload = message.get("payload")
                if not isinstance(payload, dict):
                    await _send_error(
                        websocket,
                        task_id=task_id,
                        chat_id=chat_id,
                        error_code="PAYLOAD_NOT_FOUND",
                        message="A task request payload is required",
                    )
                    continue

                memory_context = payload.get("memory_context")
                if (
                    msg_type == "task_request"
                    and memory_context is not None
                    and not isinstance(memory_context, str)
                ):
                    await _send_error(
                        websocket,
                        task_id=task_id,
                        chat_id=chat_id,
                        error_code="INVALID_MEMORY_CONTEXT",
                        message="payload.memory_context must be a string or null",
                    )
                    continue

                query = payload.get("query")
                if msg_type == "compression" and (
                    not isinstance(query, str) or not query.strip()
                ):
                    # Compression requests are standalone summarization tasks.
                    # Manual/command requests intentionally have no user query;
                    # this label is used only for task admission and persistence.
                    query = "Context compression"
                elif not isinstance(query, str) or not query.strip():
                    await _send_error(
                        websocket,
                        task_id=task_id,
                        chat_id=chat_id,
                        error_code="MISSING_REQUIRED_FIELD",
                        message="A non-empty payload.query is required",
                    )
                    continue

                runner_factory = (
                    lambda request=message,
                    current_task_id=task_id,
                    current_runtime_task_id=runtime_task_id,
                    current_chat_id=chat_id,
                    current_query=query,
                    current_compression_id=compression_id: handle_query(
                        request,
                        current_task_id,
                        current_runtime_task_id,
                        current_chat_id,
                        current_query,
                        current_compression_id,
                    )
                )

                try:
                    admission = await task_scheduler.submit(
                        task_id=runtime_task_id,
                        user_id=user_id,
                        chat_id=chat_id,
                        query=query,
                        websocket=websocket,
                        pool=pool,
                        runner_factory=runner_factory,
                        emit_status=msg_type != "compression",
                        compression_trigger=(
                            str(payload.get("trigger") or "manual")
                            if msg_type == "compression"
                            else None
                        ),
                        client_task_id=(
                            task_id if msg_type == "compression" else None
                        ),
                        compression_id=(
                            compression_id if msg_type == "compression" else None
                        ),
                    )
                except Exception:
                    if msg_type == "compression":
                        await _send_compression_failure(
                            websocket,
                            task_id=task_id,
                            chat_id=chat_id,
                            compression_id=compression_id,
                            error_code="TASK_CREATION_FAILED",
                            message="The compression task could not be created",
                            trigger_reason=str(payload.get("trigger") or "manual"),
                        )
                    else:
                        await _send_error(
                            websocket,
                            task_id=task_id,
                            chat_id=chat_id,
                            error_code="TASK_CREATION_FAILED",
                            message="The task could not be created",
                        )
                    continue

                if not admission.accepted:
                    error_code, error_message = _admission_error(admission.reason)
                    if admission.conflicting_task_id:
                        error_message += (
                            f" (active task: {admission.conflicting_task_id})"
                        )
                    if msg_type == "compression":
                        await _send_compression_failure(
                            websocket,
                            task_id=task_id,
                            chat_id=chat_id,
                            compression_id=compression_id,
                            error_code=error_code,
                            message=error_message,
                            trigger_reason=str(payload.get("trigger") or "manual"),
                        )
                    else:
                        await _send_error(
                            websocket,
                            task_id=task_id,
                            chat_id=chat_id,
                            error_code=error_code,
                            message=error_message,
                        )
                continue

            if not isinstance(task_id, str) or not task_id:
                await _send_error(
                    websocket,
                    task_id=None,
                    chat_id=chat_id,
                    error_code="MISSING_TASK_ID",
                    message=f"task_id is required for message type {msg_type!r}",
                )
                continue

            control_task_id = task_id
            if msg_type in {"cancel", "pause", "resume"}:
                control_compression_id = message.get("compression_id")
                if control_compression_id is not None:
                    try:
                        control_task_id = normalize_compression_id(
                            control_compression_id
                        )
                    except ValueError as exc:
                        await _send_error(
                            websocket,
                            task_id=task_id,
                            chat_id=chat_id,
                            error_code="INVALID_COMPRESSION_ID",
                            message=str(exc),
                        )
                        continue

            handled = False
            if msg_type == "cancel":
                handled = await task_scheduler.cancel(control_task_id, user_id)
            elif msg_type == "pause":
                handled = await task_scheduler.pause(control_task_id, user_id)
            elif msg_type == "resume":
                handled = await task_scheduler.resume(control_task_id, user_id)
            elif msg_type == "user_input":
                handled = await task_scheduler.provide_input(
                    task_id,
                    user_id,
                    message.get("data"),
                )
            elif msg_type == "client_tool_response":
                handled = await task_scheduler.provide_input(
                    task_id,
                    user_id,
                    message,
                )
                if not handled:
                    # A response can legitimately arrive after its task or
                    # tool-call deadline. It is stale, not a new UI error.
                    logger.info(
                        "Ignoring stale client tool response task_id=%s tool_call_id=%s",
                        task_id,
                        (message.get("payload") or {}).get("tool_call_id"),
                    )
                    continue
            else:
                await _send_error(
                    websocket,
                    task_id=task_id,
                    chat_id=chat_id,
                    error_code="UNKNOWN_MESSAGE_TYPE",
                    message=f"Unknown message type: {msg_type}",
                )
                continue

            if not handled:
                await _send_error(
                    websocket,
                    task_id=task_id,
                    chat_id=chat_id,
                    error_code="TASK_NOT_FOUND",
                    message="Task was not found, is not active, or is not owned by you",
                )

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected: %s:%s", client_host, client_port)
    finally:
        await task_scheduler.disconnect(websocket)
        manager.disconnect(websocket)
