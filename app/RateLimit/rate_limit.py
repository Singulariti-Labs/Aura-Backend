import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal, Optional

from asyncpg import Pool

from app.RateLimit.rate_limit_service import DEFAULT_LIMIT_USD
from app.helper import format_reset_time

RATE_LIMIT_WINDOW = timedelta(hours=12)
DEFAULT_TOKEN_COUNT = 0
DEFAULT_SPENT_USD = Decimal("0")

RateLimitStatus = Literal["active", "blocked"]


@dataclass(frozen=True)
class RateLimitDecision:
    """
    Result of checking whether a user can start a new request.

    allowed controls the websocket request flow. reset_at/reset_at_display are
    used when the request is blocked so the client can show when usage opens.
    """
    allowed: bool
    status: RateLimitStatus
    reset_at: datetime
    reset_at_display: str


async def check_rate_limit_for_request(
    *,
    pool: Pool,
    user_id: str,
    timezone_name: Optional[str] = None,
) -> RateLimitDecision:
    """
    Checks and maintains the user's 12-hour rate-limit window.

    If no row exists, this creates the first active window from now. If the
    stored window is older than 12 hours, this refreshes it and allows the
    request. If the active window has spent its full USD limit, this marks the
    row blocked and returns a blocked decision.
    """
    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                user_id,
            )

            row = await conn.fetchrow(
                """
                SELECT
                    id,
                    user_id,
                    window_start,
                    window_input_tokens,
                    window_output_tokens,
                    window_spent_usd,
                    limit_usd,
                    status,
                    updated_at
                FROM rate_limits
                WHERE user_id = $1
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                user_id,
            )

            if row is None:
                return await _create_default_window(
                    conn=conn,
                    user_id=user_id,
                    now=now,
                    timezone_name=timezone_name,
                )

            window_start = _as_utc_datetime(row["window_start"] or now)
            reset_at = window_start + RATE_LIMIT_WINDOW

            if now - window_start >= RATE_LIMIT_WINDOW:
                return await _refresh_window(
                    conn=conn,
                    user_id=user_id,
                    now=now,
                    timezone_name=timezone_name,
                )

            limit_usd = _decimal_or_default(row["limit_usd"], DEFAULT_LIMIT_USD)
            window_spent_usd = _decimal_or_default(
                row["window_spent_usd"],
                DEFAULT_SPENT_USD,
            )

            if window_spent_usd < limit_usd:
                await _set_status(
                    conn=conn,
                    user_id=user_id,
                    status="active",
                    now=now,
                )
                return _decision(
                    allowed=True,
                    status="active",
                    reset_at=reset_at,
                    timezone_name=timezone_name,
                )

            await _set_status(
                conn=conn,
                user_id=user_id,
                status="blocked",
                now=now,
            )
            return _decision(
                allowed=False,
                status="blocked",
                reset_at=reset_at,
                timezone_name=timezone_name,
            )


async def _create_default_window(
    *,
    conn,
    user_id: str,
    now: datetime,
    timezone_name: Optional[str],
) -> RateLimitDecision:
    """
    Creates the user's first rate-limit row with default active counters.
    """
    db_now = _as_db_timestamp(now)
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
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """,
        str(uuid.uuid4()),
        user_id,
        db_now,
        DEFAULT_TOKEN_COUNT,
        DEFAULT_TOKEN_COUNT,
        DEFAULT_SPENT_USD,
        DEFAULT_LIMIT_USD,
        "active",
        db_now,
    )

    return _decision(
        allowed=True,
        status="active",
        reset_at=now + RATE_LIMIT_WINDOW,
        timezone_name=timezone_name,
    )


async def _refresh_window(
    *,
    conn,
    user_id: str,
    now: datetime,
    timezone_name: Optional[str],
) -> RateLimitDecision:
    """
    Resets an expired 12-hour window and reactivates the user.
    """
    db_now = _as_db_timestamp(now)
    await conn.execute(
        """
        UPDATE rate_limits
        SET
            window_start = $2,
            window_input_tokens = $3,
            window_output_tokens = $4,
            window_spent_usd = $5,
            limit_usd = COALESCE(limit_usd, $6),
            status = $7,
            updated_at = $8
        WHERE user_id = $1
        """,
        user_id,
        db_now,
        DEFAULT_TOKEN_COUNT,
        DEFAULT_TOKEN_COUNT,
        DEFAULT_SPENT_USD,
        DEFAULT_LIMIT_USD,
        "active",
        db_now,
    )

    return _decision(
        allowed=True,
        status="active",
        reset_at=now + RATE_LIMIT_WINDOW,
        timezone_name=timezone_name,
    )


async def _set_status(
    *,
    conn,
    user_id: str,
    status: RateLimitStatus,
    now: datetime,
) -> None:
    """
    Persists the current active/blocked status without changing usage counters.
    """
    db_now = _as_db_timestamp(now)
    await conn.execute(
        """
        UPDATE rate_limits
        SET status = $2, updated_at = $3
        WHERE user_id = $1
        """,
        user_id,
        status,
        db_now,
    )


def _decision(
    *,
    allowed: bool,
    status: RateLimitStatus,
    reset_at: datetime,
    timezone_name: Optional[str],
) -> RateLimitDecision:
    """
    Builds a decision with both raw UTC reset time and formatted display time.
    """
    return RateLimitDecision(
        allowed=allowed,
        status=status,
        reset_at=reset_at,
        reset_at_display=format_reset_time(reset_at, timezone_name),
    )


def _decimal_or_default(value, default: Decimal) -> Decimal:
    """
    Normalizes asyncpg numeric values to Decimal for safe money comparison.
    """
    if value is None:
        return default
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _as_utc_datetime(value: datetime) -> datetime:
    """
    Normalizes database timestamps to timezone-aware UTC datetimes.

    Postgres may return aware datetimes for TIMESTAMPTZ and naive datetimes for
    TIMESTAMP. Naive values are treated as UTC because this project stores UTC.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _as_db_timestamp(value: datetime) -> datetime:
    """
    Converts an internal UTC datetime to naive UTC for existing DB columns.
    """
    return _as_utc_datetime(value).replace(tzinfo=None)
