from typing import Optional

from asyncpg import Pool


async def get_user_token_usage(pool: Pool, user_id: str) -> Optional[dict]:
    """Fetch the user's latest cumulative token-usage row."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                total_input_tokens,
                total_output_tokens,
                total_spent_usd,
                updated_at
            FROM user_token_usage
            WHERE user_id = $1
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            user_id,
        )
    return dict(row) if row else None


async def get_user_rate_limit(pool: Pool, user_id: str) -> Optional[dict]:
    """Fetch the user's latest rate-limit window."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
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
    return dict(row) if row else None
