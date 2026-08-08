import asyncio
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from asyncpg import Pool

from app.DB.Queries.user_usage import get_user_rate_limit, get_user_token_usage
from app.RateLimit.rate_limit import RATE_LIMIT_WINDOW
from app.RateLimit.rate_limit_service import DEFAULT_LIMIT_USD


async def fetch_user_usage(pool: Pool, user_id: str) -> dict:
    """
    Return cumulative and current-window usage for a user.

    The independent table reads intentionally run concurrently so endpoint
    latency is bounded by the slower query rather than the sum of both.
    """
    overall_row, rate_limit_row = await asyncio.gather(
        get_user_token_usage(pool, user_id),
        get_user_rate_limit(pool, user_id),
    )

    overall_input_tokens = _safe_int(
        _get(overall_row, "total_input_tokens")
    )
    overall_output_tokens = _safe_int(
        _get(overall_row, "total_output_tokens")
    )
    window_input_tokens = _safe_int(
        _get(rate_limit_row, "window_input_tokens")
    )
    window_output_tokens = _safe_int(
        _get(rate_limit_row, "window_output_tokens")
    )
    window_spent_usd = _safe_decimal(
        _get(rate_limit_row, "window_spent_usd")
    )
    limit_usd = _safe_decimal(
        _get(rate_limit_row, "limit_usd"),
        default=DEFAULT_LIMIT_USD,
    )
    window_start = _as_utc_datetime(_get(rate_limit_row, "window_start"))

    return {
        "overall_usage": {
            "input_tokens": overall_input_tokens,
            "output_tokens": overall_output_tokens,
            "total_tokens": overall_input_tokens + overall_output_tokens,
            "spent_usd": _safe_decimal(
                _get(overall_row, "total_spent_usd")
            ),
        },
        "rate_limit": {
            "window_start": window_start,
            "reset_at": window_start + RATE_LIMIT_WINDOW if window_start else None,
            "input_tokens": window_input_tokens,
            "output_tokens": window_output_tokens,
            "total_tokens": window_input_tokens + window_output_tokens,
            "spent_usd": window_spent_usd,
            "limit_usd": limit_usd,
            "remaining_usd": max(limit_usd - window_spent_usd, Decimal("0")),
            "status": _get(rate_limit_row, "status") or "active",
        },
    }


def _get(row: Optional[dict], key: str) -> Any:
    return row.get(key) if row else None


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_decimal(value: Any, *, default: Decimal = Decimal("0")) -> Decimal:
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _as_utc_datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
