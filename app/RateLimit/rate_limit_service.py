import asyncio
from concurrent.futures import CancelledError as FutureCancelledError
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from asyncpg import Pool

from app.RateLimit.token_pricing import calculate_token_cost_usd

logger = logging.getLogger(__name__)

DEFAULT_DB_TIMEOUT_SECONDS = float(os.getenv("RATE_LIMIT_DB_TIMEOUT_SECONDS", "10"))
DEFAULT_RETRY_DELAY_SECONDS = float(os.getenv("RATE_LIMIT_RETRY_DELAY_SECONDS", "1"))
DEFAULT_LIMIT_USD = Decimal(os.getenv("RATE_LIMIT_DEFAULT_LIMIT_USD", "3"))
_BACKGROUND_TASKS: set[asyncio.Task] = set()
_BACKGROUND_FUTURES: set[Any] = set()
@dataclass(frozen=True)
class TokenUsageEvent:
    user_id: str
    input_tokens: int
    output_tokens: int
    spent_usd: Decimal
    provider: Optional[str] = None
    model_name: Optional[str] = None
    credential_source: str = "platform"


def schedule_token_usage_update(
    *,
    pool: Optional[Pool],
    user_id: Optional[str],
    usage: Optional[dict],
    details: Optional[dict] = None,
    event_loop: Optional[asyncio.AbstractEventLoop] = None,
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

    coroutine = _record_token_usage_with_retry(
        pool=pool,
        event=event,
        timeout_seconds=timeout_seconds,
        retry_delay_seconds=retry_delay_seconds,
    )

    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    if event_loop is not None and event_loop.is_running():
        if running_loop is event_loop:
            task = running_loop.create_task(coroutine)
            _BACKGROUND_TASKS.add(task)
            task.add_done_callback(_log_background_task_error)
            return

        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, event_loop)
            _BACKGROUND_FUTURES.add(future)
            future.add_done_callback(_log_threadsafe_future_error)
            return
        except RuntimeError:
            coroutine.close()
            logger.exception(
                "Unable to schedule token usage update on captured event loop"
            )
            return

    if running_loop is not None:
        task = running_loop.create_task(coroutine)
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_log_background_task_error)
        return

    coroutine.close()
    logger.error(
        "Unable to schedule token usage update; no running event loop available"
    )


async def drain_token_usage_updates(timeout_seconds: float = 10) -> None:
    """Give in-flight local usage writes time to finish during clean shutdown.

    This does not affect request latency. It prevents normal deployments and
    graceful restarts from closing the database pool while accounting tasks are
    still committing their usage ledger records.
    """

    pending_tasks = tuple(task for task in _BACKGROUND_TASKS if not task.done())
    if not pending_tasks:
        return
    done, pending = await asyncio.wait(pending_tasks, timeout=timeout_seconds)
    for task in done:
        try:
            task.result()
        except (asyncio.CancelledError, Exception):
            logger.exception("Usage update failed while draining during shutdown")
    if pending:
        logger.warning(
            "%d usage update task(s) did not finish before shutdown",
            len(pending),
        )


def _build_usage_event(
    *,
    user_id: Optional[str],
    usage: Optional[dict],
    details: Optional[dict],
) -> Optional[TokenUsageEvent]:
    """
    Builds a normalized usage event and fills missing spend from token pricing.

    Handler metadata should already include cost, but this fallback keeps DB
    writes correct if a provider returns tokens without a spend value.
    """
    if not user_id or not usage:
        return None

    input_tokens = _safe_int(usage.get("input"))
    output_tokens = _safe_int(usage.get("output"))
    spent_usd = _safe_decimal(
        usage.get("spent_usd", usage.get("cost", usage.get("total_spent_usd", 0)))
    )

    details = details or {}
    provider = details.get("provider")
    model_name = details.get("model_name")
    credential_source = str(details.get("credential_source") or "platform")
    if credential_source not in {"platform", "custom"}:
        credential_source = "platform"

    if spent_usd == 0 and (input_tokens > 0 or output_tokens > 0):
        spent_usd = calculate_token_cost_usd(
            provider=provider,
            model_name=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    # Custom/BYOK calls are useful for token analytics but do not consume the
    # platform-funded subscription allowance.
    if credential_source == "custom":
        spent_usd = Decimal("0")

    if input_tokens == 0 and output_tokens == 0 and spent_usd == 0:
        return None

    return TokenUsageEvent(
        user_id=user_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        spent_usd=spent_usd,
        provider=provider,
        model_name=model_name,
        credential_source=credential_source,
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
    now = datetime.now(timezone.utc)
    db_now = _as_db_timestamp(now)
    row_id = str(uuid.uuid4())
    usage_id = str(uuid.uuid4())

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                event.user_id,
            )

            rate_limit_row = await conn.fetchrow(
                """
                SELECT id, plan_code, window_spent_usd, limit_usd
                FROM rate_limits
                WHERE user_id = $1
                FOR UPDATE
                """,
                event.user_id,
            )

            if rate_limit_row is None:
                plan = await conn.fetchrow(
                    """
                    SELECT
                        users.plan_code,
                        plans.usage_limit_usd,
                        CASE
                            WHEN billing.entitlement_source = 'promo'
                                THEN billing.access_until
                            ELSE NULL
                        END AS plan_expires_at
                    FROM users
                    JOIN subscription_plans AS plans
                      ON plans.code = users.plan_code
                    LEFT JOIN user_billing AS billing
                      ON billing.user_id = users.id
                    WHERE users.id = $1
                    """,
                    event.user_id,
                )
                plan_code = str(plan["plan_code"] if plan else "free")
                limit_usd = _safe_decimal(
                    plan["usage_limit_usd"] if plan else DEFAULT_LIMIT_USD
                ) or DEFAULT_LIMIT_USD
                await conn.execute(
                    """
                    INSERT INTO rate_limits (
                        id,
                        user_id,
                        plan_code,
                        plan_expires_at,
                        window_start,
                        window_input_tokens,
                        window_output_tokens,
                        window_spent_usd,
                        limit_usd,
                        status,
                        block_reason,
                        updated_at
                    )
                    VALUES ($1, $2, $3, $4, $5, 0, 0, 0, $6, 'active', NULL, $5)
                    """,
                    row_id,
                    event.user_id,
                    plan_code,
                    plan["plan_expires_at"] if plan else None,
                    db_now,
                    limit_usd,
                )
                rate_limit_row = {
                    "id": row_id,
                    "plan_code": plan_code,
                    "window_spent_usd": Decimal("0"),
                    "limit_usd": limit_usd,
                }

            limit_usd = _safe_decimal(rate_limit_row["limit_usd"]) or DEFAULT_LIMIT_USD
            current_spend = _safe_decimal(rate_limit_row["window_spent_usd"])
            updated_spend = current_spend + event.spent_usd
            rate_status = _status_for_spend(updated_spend, limit_usd)

            await conn.execute(
                """
                UPDATE rate_limits
                SET
                    window_input_tokens = window_input_tokens + $2,
                    window_output_tokens = window_output_tokens + $3,
                    window_spent_usd = $4,
                    status = $5,
                    block_reason = $6,
                    updated_at = $7
                WHERE id = $1
                """,
                rate_limit_row["id"],
                event.input_tokens,
                event.output_tokens,
                updated_spend,
                rate_status,
                "usage_limit" if rate_status == "blocked" else None,
                db_now,
            )

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
                ON CONFLICT (user_id) DO UPDATE
                SET
                    total_input_tokens = user_token_usage.total_input_tokens
                        + EXCLUDED.total_input_tokens,
                    total_output_tokens = user_token_usage.total_output_tokens
                        + EXCLUDED.total_output_tokens,
                    total_spent_usd = user_token_usage.total_spent_usd
                        + EXCLUDED.total_spent_usd,
                    updated_at = EXCLUDED.updated_at
                """,
                usage_id,
                event.user_id,
                event.input_tokens,
                event.output_tokens,
                event.spent_usd,
                db_now,
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


def _status_for_spend(spent_usd: Decimal, limit_usd: Decimal) -> str:
    """
    Converts the current window spend into the persisted rate-limit status.
    """
    return "blocked" if spent_usd >= limit_usd else "active"


def _as_db_timestamp(value: datetime) -> datetime:
    """
    Return an aware UTC timestamp for TIMESTAMPTZ database columns.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _log_background_task_error(task: asyncio.Task) -> None:
    _BACKGROUND_TASKS.discard(task)
    try:
        task.result()
    except asyncio.CancelledError:
        logger.warning("Token usage update task was cancelled")
    except Exception:
        logger.exception("Unhandled token usage update task error")


def _log_threadsafe_future_error(future: Any) -> None:
    _BACKGROUND_FUTURES.discard(future)
    try:
        future.result()
    except FutureCancelledError:
        logger.warning("Token usage update future was cancelled")
    except Exception:
        logger.exception("Unhandled token usage update future error")
