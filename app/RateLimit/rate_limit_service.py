import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from asyncpg import Pool

logger = logging.getLogger(__name__)

DEFAULT_DB_TIMEOUT_SECONDS = float(os.getenv("RATE_LIMIT_DB_TIMEOUT_SECONDS", "10"))
DEFAULT_RETRY_DELAY_SECONDS = float(os.getenv("RATE_LIMIT_RETRY_DELAY_SECONDS", "1"))
DEFAULT_LIMIT_USD = Decimal(os.getenv("RATE_LIMIT_DEFAULT_LIMIT_USD", "3"))


@dataclass(frozen=True)
class TokenUsageEvent:
    user_id: str
    input_tokens: int
    output_tokens: int
    spent_usd: Decimal
    provider: Optional[str] = None
    model_name: Optional[str] = None


def schedule_token_usage_update(
    *,
    pool: Optional[Pool],
    user_id: Optional[str],
    usage: Optional[dict],
    details: Optional[dict] = None,
    timeout_seconds: float = DEFAULT_DB_TIMEOUT_SECONDS,
    retry_delay_seconds: float = DEFAULT_RETRY_DELAY_SECONDS,
) -> None:
    """
    Fire-and-forget token usage persistence.

    This intentionally does not await the DB write, so websocket/agent response
    flow is not delayed by rate-limit bookkeeping.
    """
    event = _build_usage_event(user_id=user_id, usage=usage, details=details)
    if pool is None or event is None:
        return

    try:
        task = asyncio.create_task(
            _record_token_usage_with_retry(
                pool=pool,
                event=event,
                timeout_seconds=timeout_seconds,
                retry_delay_seconds=retry_delay_seconds,
            )
        )
        task.add_done_callback(_log_background_task_error)
    except RuntimeError:
        logger.exception("Unable to schedule token usage update; no running event loop")


def _build_usage_event(
    *,
    user_id: Optional[str],
    usage: Optional[dict],
    details: Optional[dict],
) -> Optional[TokenUsageEvent]:
    if not user_id or not usage:
        return None

    input_tokens = _safe_int(usage.get("input"))
    output_tokens = _safe_int(usage.get("output"))
    spent_usd = _safe_decimal(
        usage.get("spent_usd", usage.get("cost", usage.get("total_spent_usd", 0)))
    )

    if input_tokens == 0 and output_tokens == 0 and spent_usd == 0:
        return None

    details = details or {}
    return TokenUsageEvent(
        user_id=user_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        spent_usd=spent_usd,
        provider=details.get("provider"),
        model_name=details.get("model_name"),
    )


async def _record_token_usage_with_retry(
    *,
    pool: Pool,
    event: TokenUsageEvent,
    timeout_seconds: float,
    retry_delay_seconds: float,
) -> None:
    last_error: Optional[BaseException] = None

    for attempt in range(1, 3):
        try:
            await asyncio.wait_for(
                _record_token_usage(pool=pool, event=event),
                timeout=timeout_seconds,
            )
            return
        except Exception as exc:
            last_error = exc
            if attempt == 1:
                logger.warning(
                    "Token usage update attempt failed for user_id=%s provider=%s model=%s; retrying once",
                    event.user_id,
                    event.provider,
                    event.model_name,
                    exc_info=True,
                )
                await asyncio.sleep(retry_delay_seconds)

    exc_info = (
        (type(last_error), last_error, last_error.__traceback__)
        if last_error
        else None
    )
    logger.error(
        "Token usage update failed after retry for user_id=%s provider=%s model=%s",
        event.user_id,
        event.provider,
        event.model_name,
        exc_info=exc_info,
    )


async def _record_token_usage(*, pool: Pool, event: TokenUsageEvent) -> None:
    now = datetime.utcnow()
    row_id = str(uuid.uuid4())
    usage_id = str(uuid.uuid4())

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                event.user_id,
            )

            rate_limit_status = await conn.execute(
                """
                UPDATE rate_limits
                SET
                    window_input_tokens = COALESCE(window_input_tokens, 0) + $2,
                    window_output_tokens = COALESCE(window_output_tokens, 0) + $3,
                    window_spent_usd = COALESCE(window_spent_usd, 0) + $4,
                    limit_usd = COALESCE(limit_usd, $5),
                    status = CASE
                        WHEN COALESCE(window_spent_usd, 0) + $4 >= COALESCE(limit_usd, $5)
                        THEN 'blocked'
                        ELSE 'active'
                    END,
                    updated_at = $6
                WHERE user_id = $1
                """,
                event.user_id,
                event.input_tokens,
                event.output_tokens,
                event.spent_usd,
                DEFAULT_LIMIT_USD,
                now,
            )

            if _rows_affected(rate_limit_status) == 0:
                await conn.execute(
                    """
                    INSERT INTO rate_limits (
                        id,
                        user_id,
                        window_start,
                        window_input_tokens,
                        window_output_tokens,
                        window_spent_usd,
                        limit_usd,
                        status,
                        updated_at
                    )
                    VALUES (
                        $1,
                        $2,
                        $3,
                        $4,
                        $5,
                        $6,
                        $7,
                        CASE WHEN $6 >= $7 THEN 'blocked' ELSE 'active' END,
                        $8
                    )
                    """,
                    row_id,
                    event.user_id,
                    now,
                    event.input_tokens,
                    event.output_tokens,
                    event.spent_usd,
                    DEFAULT_LIMIT_USD,
                    now,
                )

            usage_status = await conn.execute(
                """
                UPDATE user_token_usage
                SET
                    total_input_tokens = COALESCE(total_input_tokens, 0) + $2,
                    total_output_tokens = COALESCE(total_output_tokens, 0) + $3,
                    total_spent_usd = COALESCE(total_spent_usd, 0) + $4,
                    updated_at = $5
                WHERE user_id = $1
                """,
                event.user_id,
                event.input_tokens,
                event.output_tokens,
                event.spent_usd,
                now,
            )

            if _rows_affected(usage_status) == 0:
                await conn.execute(
                    """
                    INSERT INTO user_token_usage (
                        id,
                        user_id,
                        total_input_tokens,
                        total_output_tokens,
                        total_spent_usd,
                        updated_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    usage_id,
                    event.user_id,
                    event.input_tokens,
                    event.output_tokens,
                    event.spent_usd,
                    now,
                )


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _rows_affected(command_status: str) -> int:
    try:
        return int(command_status.rsplit(" ", 1)[-1])
    except (AttributeError, ValueError):
        return 0


def _log_background_task_error(task: asyncio.Task) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        logger.warning("Token usage update task was cancelled")
    except Exception:
        logger.exception("Unhandled token usage update task error")
