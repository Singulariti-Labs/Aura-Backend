from asyncpg import Pool
from datetime import datetime, timezone
import uuid
from typing import Optional
from app.DB.models import User

async def get_user(pool: Pool, user_id: str) -> Optional[dict]:
    """
    Fetch user details by user_id.
    """
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    id,
                    email,
                    name,
                    plan_code,
                    plan_updated_at,
                    created_at,
                    updated_at
                FROM users
                WHERE id = $1
                """,
                user_id
            )
            if row:
                return dict(row)
            return None
    except Exception as e:
        print(f"❌ FETCH USER FAILED: {e}")
        return None

async def get_user_by_auth0_id(pool: Pool, auth0_id: str) -> Optional[dict]:
    """
    Fetch user by Auth0 ID.
    """
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM users WHERE auth0_id = $1",
                auth0_id
            )
            return dict(row) if row else None
    except Exception as e:
        print(f"❌ FETCH USER BY AUTH0 ID FAILED: {e}")
        return None

async def sync_user(pool: Pool, user_data: dict) -> dict:
    """
    Atomically sync an Auth0 profile and initialize Free-plan billing data.

    The Auth0 subject advisory lock and unique index make concurrent login
    callbacks safe.  Creating the user, billing row, and rate-limit row in one
    transaction guarantees that the first task request never needs an extra
    subscription lookup or lazy billing initialization.
    """
    auth0_id = user_data.get("sub")
    if not auth0_id:
        raise ValueError("Auth0 payload is missing sub")

    email = user_data.get("email") or f"{auth0_id}@placeholder.com" # Fallback to prevent DB error
    name = user_data.get("name") or email.split("@")[0]
    user_id = user_data.get("user_id") or str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext($1))",
                    auth0_id,
                )

                user = await conn.fetchrow(
                    """
                    INSERT INTO users (
                        id,
                        auth0_id,
                        email,
                        name,
                        plan_code,
                        plan_updated_at,
                        created_at,
                        updated_at
                    )
                    VALUES ($1, $2, $3, $4, 'free', $5, $5, $5)
                    ON CONFLICT (auth0_id)
                    WHERE auth0_id IS NOT NULL
                    DO UPDATE
                    SET
                        email = EXCLUDED.email,
                        name = EXCLUDED.name,
                        updated_at = EXCLUDED.updated_at
                    RETURNING *
                    """,
                    user_id,
                    auth0_id,
                    email,
                    name,
                    now,
                )
                resolved_user_id = str(user["id"])

                await conn.execute(
                    """
                    INSERT INTO user_billing (
                        user_id,
                        plan_code,
                        entitlement_status,
                        access_started_at
                    )
                    VALUES ($1, 'free', 'active', $2)
                    ON CONFLICT (user_id) DO NOTHING
                    """,
                    resolved_user_id,
                    now,
                )

                await conn.execute(
                    """
                    INSERT INTO rate_limits (
                        id,
                        user_id,
                        plan_code,
                        window_start,
                        window_input_tokens,
                        window_output_tokens,
                        window_spent_usd,
                        limit_usd,
                        status,
                        block_reason,
                        updated_at
                    )
                    SELECT
                        $1,
                        $2,
                        plans.code,
                        $3,
                        0,
                        0,
                        0,
                        plans.usage_limit_usd,
                        'active',
                        NULL,
                        $3
                    FROM subscription_plans AS plans
                    WHERE plans.code = 'free'
                    ON CONFLICT (user_id) DO NOTHING
                    """,
                    str(uuid.uuid4()),
                    resolved_user_id,
                    now,
                )

                return dict(user)
    except Exception as e:
        print(f"❌ SYNC USER FAILED: {e}")
        raise
