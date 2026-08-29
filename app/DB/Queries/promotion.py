"""Database operations for secure promotional plan access.

All multi-table entitlement changes live in this module so callers cannot
partially update a user, their billing snapshot, or their rate-limit cache.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Optional

from asyncpg import Connection, Pool


PLAN_RANK = {"free": 0, "mini": 1, "pro": 2, "max": 3}


async def create_promo_code(
    pool: Pool,
    *,
    code_hash: str,
    code_hint: str,
    plan_code: str,
    valid_from: datetime,
    valid_until: Optional[datetime],
    access_duration_days: Optional[int],
    max_redemptions: Optional[int],
    metadata: Optional[dict] = None,
) -> dict:
    """Insert one promo definition and return its non-secret database fields.

    The caller owns the plaintext code and must display it only once. This
    function receives and stores only its keyed hash and a masked hint.
    """

    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            INSERT INTO promo_codes (
                id,
                code_hash,
                code_hint,
                plan_code,
                valid_from,
                valid_until,
                access_duration_days,
                max_redemptions,
                metadata
            )
            SELECT
                $1,
                $2,
                $3,
                plans.code,
                $5,
                $6,
                $7,
                $8,
                $9::JSONB
            FROM subscription_plans AS plans
            WHERE plans.code = $4
              AND plans.active = TRUE
              AND plans.code <> 'free'
            RETURNING
                id,
                code_hint,
                plan_code,
                active,
                valid_from,
                valid_until,
                access_duration_days,
                max_redemptions,
                redemption_count,
                metadata,
                created_at
            """,
            str(uuid.uuid4()),
            code_hash,
            code_hint,
            plan_code,
            valid_from,
            valid_until,
            access_duration_days,
            max_redemptions,
            json.dumps(metadata or {}),
        )
    if row is None:
        raise ValueError(f"Unknown or inactive paid plan: {plan_code}")
    return _normalize_json(dict(row), "metadata")


async def redeem_promo_code_transaction(
    pool: Pool,
    *,
    user_id: str,
    code_hash: str,
    now: datetime,
) -> dict:
    """Validate and redeem a promo while atomically updating entitlement.

    The promo row is locked before checking its redemption counter, and a
    per-user advisory lock serializes competing entitlement changes. Existing
    12-hour spend is preserved so redeeming a code cannot reset usage.

    Returns an ``outcome`` value of ``redeemed``, ``invalid``, ``conflict``,
    or ``user_missing``. Public services deliberately collapse detailed promo
    failures into a generic invalid-code response.
    """

    async with pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                user_id,
            )

            billing = await connection.fetchrow(
                """
                SELECT
                    plan_code,
                    entitlement_source,
                    access_until,
                    promo_redemption_id
                FROM user_billing
                WHERE user_id = $1
                FOR UPDATE
                """,
                user_id,
            )
            if billing is None:
                return {"outcome": "user_missing"}

            promo = await connection.fetchrow(
                """
                SELECT
                    promo.id,
                    promo.plan_code,
                    promo.active,
                    promo.valid_from,
                    promo.valid_until,
                    promo.access_duration_days,
                    promo.max_redemptions,
                    promo.redemption_count,
                    plans.usage_limit_usd
                FROM promo_codes AS promo
                JOIN subscription_plans AS plans
                  ON plans.code = promo.plan_code
                WHERE promo.code_hash = $1
                  AND plans.active = TRUE
                FOR UPDATE OF promo
                """,
                code_hash,
            )
            if promo is None or not promo["active"]:
                return {"outcome": "invalid"}
            if now < promo["valid_from"]:
                return {"outcome": "invalid"}
            if promo["valid_until"] and now >= promo["valid_until"]:
                return {"outcome": "invalid"}
            if (
                promo["max_redemptions"] is not None
                and promo["redemption_count"] >= promo["max_redemptions"]
            ):
                return {"outcome": "invalid"}

            previous_redemption = await connection.fetchval(
                """
                SELECT id
                FROM promo_redemptions
                WHERE promo_code_id = $1 AND user_id = $2
                """,
                promo["id"],
                user_id,
            )
            if previous_redemption is not None:
                return {"outcome": "invalid"}

            current_plan = str(billing["plan_code"] or "free")
            current_source = str(billing["entitlement_source"] or "free")
            current_access_until = billing["access_until"]
            current_promo_active = current_source == "promo" and (
                current_access_until is None or current_access_until > now
            )

            # Payment and admin grants have higher authority than promotions.
            if current_source in {"payment", "admin"} and current_plan != "free":
                return {"outcome": "conflict"}

            new_plan = str(promo["plan_code"])
            if current_promo_active:
                if PLAN_RANK.get(new_plan, 0) <= PLAN_RANK.get(current_plan, 0):
                    return {"outcome": "conflict"}
                await _close_current_promo(
                    connection,
                    redemption_id=billing["promo_redemption_id"],
                    status="revoked",
                    now=now,
                )
            elif current_source == "promo":
                await _close_current_promo(
                    connection,
                    redemption_id=billing["promo_redemption_id"],
                    status="expired",
                    now=now,
                )
            elif PLAN_RANK.get(current_plan, 0) >= PLAN_RANK.get(new_plan, 0):
                return {"outcome": "conflict"}

            duration_days = promo["access_duration_days"]
            access_expires_at = (
                now + timedelta(days=int(duration_days))
                if duration_days is not None
                else None
            )
            redemption_id = str(uuid.uuid4())
            await connection.execute(
                """
                INSERT INTO promo_redemptions (
                    id,
                    promo_code_id,
                    user_id,
                    granted_plan_code,
                    previous_plan_code,
                    redeemed_at,
                    access_expires_at,
                    status
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, 'active')
                """,
                redemption_id,
                promo["id"],
                user_id,
                new_plan,
                current_plan,
                now,
                access_expires_at,
            )
            await connection.execute(
                """
                UPDATE promo_codes
                SET redemption_count = redemption_count + 1, updated_at = $2
                WHERE id = $1
                """,
                promo["id"],
                now,
            )
            await connection.execute(
                """
                UPDATE user_billing
                SET
                    plan_code = $2,
                    entitlement_status = 'active',
                    entitlement_source = 'promo',
                    promo_redemption_id = $3,
                    access_started_at = $4,
                    access_until = $5,
                    updated_at = $4
                WHERE user_id = $1
                """,
                user_id,
                new_plan,
                redemption_id,
                now,
                access_expires_at,
            )
            await connection.execute(
                """
                UPDATE users
                SET plan_code = $2, plan_updated_at = $3, updated_at = $3
                WHERE id = $1
                """,
                user_id,
                new_plan,
                now,
            )

            usage_limit = Decimal(str(promo["usage_limit_usd"]))
            rate_limit = await connection.fetchrow(
                """
                SELECT window_spent_usd
                FROM rate_limits
                WHERE user_id = $1
                FOR UPDATE
                """,
                user_id,
            )
            if rate_limit is None:
                await _insert_rate_limit(
                    connection,
                    user_id=user_id,
                    plan_code=new_plan,
                    usage_limit=usage_limit,
                    plan_expires_at=access_expires_at,
                    now=now,
                )
                spent = Decimal("0")
            else:
                spent = Decimal(str(rate_limit["window_spent_usd"] or 0))
                blocked = spent >= usage_limit
                await connection.execute(
                    """
                    UPDATE rate_limits
                    SET
                        plan_code = $2,
                        limit_usd = $3,
                        plan_expires_at = $4,
                        status = $5,
                        block_reason = $6,
                        updated_at = $7
                    WHERE user_id = $1
                    """,
                    user_id,
                    new_plan,
                    usage_limit,
                    access_expires_at,
                    "blocked" if blocked else "active",
                    "usage_limit" if blocked else None,
                    now,
                )

            return {
                "outcome": "redeemed",
                "plan_code": new_plan,
                "previous_plan_code": current_plan,
                "access_started_at": now,
                "access_expires_at": access_expires_at,
                "usage_limit_usd": usage_limit,
                "window_spent_usd": spent,
            }


async def expire_promotion_if_due(
    connection: Connection,
    *,
    user_id: str,
    now: datetime,
    current_spent_usd: Decimal,
) -> Optional[dict]:
    """Downgrade an expired promotional entitlement to Free in-place.

    This function is called only after the existing rate-limit query observes
    an expired cached timestamp. It uses the caller's transaction and advisory
    user lock, so normal requests incur no additional query or network call.
    """

    billing = await connection.fetchrow(
        """
        SELECT entitlement_source, access_until, promo_redemption_id
        FROM user_billing
        WHERE user_id = $1
        FOR UPDATE
        """,
        user_id,
    )
    if (
        billing is None
        or billing["entitlement_source"] != "promo"
        or billing["access_until"] is None
        or billing["access_until"] > now
    ):
        # Heal a stale cache without changing a newer payment/admin grant.
        await connection.execute(
            """
            UPDATE rate_limits
            SET plan_expires_at = NULL, updated_at = $2
            WHERE user_id = $1
            """,
            user_id,
            now,
        )
        return None

    free_plan = await connection.fetchrow(
        """
        SELECT code, usage_limit_usd
        FROM subscription_plans
        WHERE code = 'free' AND active = TRUE
        """
    )
    if free_plan is None:
        raise RuntimeError("The Free plan is missing or inactive")

    await _close_current_promo(
        connection,
        redemption_id=billing["promo_redemption_id"],
        status="expired",
        now=now,
    )
    await connection.execute(
        """
        UPDATE user_billing
        SET
            plan_code = 'free',
            entitlement_status = 'active',
            entitlement_source = 'free',
            promo_redemption_id = NULL,
            access_started_at = $2,
            access_until = NULL,
            updated_at = $2
        WHERE user_id = $1
        """,
        user_id,
        now,
    )
    await connection.execute(
        """
        UPDATE users
        SET plan_code = 'free', plan_updated_at = $2, updated_at = $2
        WHERE id = $1
        """,
        user_id,
        now,
    )

    free_limit = Decimal(str(free_plan["usage_limit_usd"]))
    blocked = current_spent_usd >= free_limit
    await connection.execute(
        """
        UPDATE rate_limits
        SET
            plan_code = 'free',
            limit_usd = $2,
            plan_expires_at = NULL,
            status = $3,
            block_reason = $4,
            updated_at = $5
        WHERE user_id = $1
        """,
        user_id,
        free_limit,
        "blocked" if blocked else "active",
        "usage_limit" if blocked else None,
        now,
    )
    return {
        "plan_code": "free",
        "limit_usd": free_limit,
        "status": "blocked" if blocked else "active",
        "block_reason": "usage_limit" if blocked else None,
    }


async def refresh_expired_user_promotion(
    pool: Pool,
    *,
    user_id: str,
    now: datetime,
) -> bool:
    """Expire promotional access before returning an account summary.

    Regular task requests already inspect the expiry in their existing
    rate-limit query. This helper gives the lower-frequency ``/me`` endpoint
    the same consistency even when the user has not started another task.
    """

    async with pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                user_id,
            )
            rate_limit = await connection.fetchrow(
                """
                SELECT plan_expires_at, window_spent_usd
                FROM rate_limits
                WHERE user_id = $1
                FOR UPDATE
                """,
                user_id,
            )
            if (
                rate_limit is None
                or rate_limit["plan_expires_at"] is None
                or rate_limit["plan_expires_at"] > now
            ):
                return False
            result = await expire_promotion_if_due(
                connection,
                user_id=user_id,
                now=now,
                current_spent_usd=Decimal(
                    str(rate_limit["window_spent_usd"] or 0)
                ),
            )
            return result is not None


async def _close_current_promo(
    connection: Connection,
    *,
    redemption_id: Any,
    status: str,
    now: datetime,
) -> None:
    """Mark the previous active redemption expired or administratively replaced."""

    if redemption_id is None:
        return
    await connection.execute(
        """
        UPDATE promo_redemptions
        SET status = $2, revoked_at = CASE WHEN $2 = 'revoked' THEN $3 ELSE NULL END
        WHERE id = $1 AND status = 'active'
        """,
        redemption_id,
        status,
        now,
    )


async def _insert_rate_limit(
    connection: Connection,
    *,
    user_id: str,
    plan_code: str,
    usage_limit: Decimal,
    plan_expires_at: Optional[datetime],
    now: datetime,
) -> None:
    """Create the exceptional missing rate-limit cache during redemption."""

    await connection.execute(
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
        str(uuid.uuid4()),
        user_id,
        plan_code,
        plan_expires_at,
        now,
        usage_limit,
    )


def _normalize_json(row: dict, *columns: str) -> dict:
    """Decode JSON strings when asyncpg uses its default JSON codec."""

    for column in columns:
        value = row.get(column)
        if isinstance(value, str):
            try:
                row[column] = json.loads(value)
            except json.JSONDecodeError:
                row[column] = {}
    return row
