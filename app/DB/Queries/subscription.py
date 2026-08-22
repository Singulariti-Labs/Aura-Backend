"""Database queries for subscription plans, billing state, and webhooks."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from asyncpg import Connection, Pool


PAID_PLAN_CODES = ("mini", "pro", "max")
PLAN_STRIPE_ENV_NAMES = {
    "mini": ("STRIPE_PRODUCT_MINI", "STRIPE_PRICE_MINI_MONTHLY"),
    "pro": ("STRIPE_PRODUCT_PRO", "STRIPE_PRICE_PRO_MONTHLY"),
    "max": ("STRIPE_PRODUCT_MAX", "STRIPE_PRICE_MAX_MONTHLY"),
}


async def sync_plan_stripe_ids_from_env(pool: Pool) -> None:
    """Copy configured Stripe Product/Price IDs into the local plan catalog.

    Stripe IDs differ between test and live environments.  Keeping them in
    environment variables while synchronizing them at startup makes the
    database catalog authoritative for request handling without hard-coding
    environment-specific identifiers in a migration.
    """

    configured_plans: list[tuple[str, Optional[str], Optional[str]]] = []
    for plan_code, (product_env, price_env) in PLAN_STRIPE_ENV_NAMES.items():
        product_id = _clean_env_value(product_env)
        price_id = _clean_env_value(price_env)
        if product_id or price_id:
            configured_plans.append((plan_code, product_id, price_id))

    if not configured_plans:
        return

    async with pool.acquire() as connection:
        async with connection.transaction():
            for plan_code, product_id, price_id in configured_plans:
                await connection.execute(
                    """
                    UPDATE subscription_plans
                    SET
                        stripe_product_id = COALESCE($2, stripe_product_id),
                        stripe_price_id = COALESCE($3, stripe_price_id),
                        updated_at = NOW()
                    WHERE code = $1
                    """,
                    plan_code,
                    product_id,
                    price_id,
                )


async def list_active_plans(pool: Pool) -> list[dict]:
    """Return the public plan catalog in display order."""

    async with pool.acquire() as connection:
        rows = await connection.fetch(
            """
            SELECT
                code,
                name,
                price_cents,
                currency,
                billing_interval,
                usage_limit_usd,
                window_hours,
                features
            FROM subscription_plans
            WHERE active = TRUE
            ORDER BY
                CASE code
                    WHEN 'free' THEN 1
                    WHEN 'mini' THEN 2
                    WHEN 'pro' THEN 3
                    WHEN 'max' THEN 4
                    ELSE 99
                END
            """
        )
    return [_normalize_json_columns(dict(row), "features") for row in rows]


async def get_plan_by_code(pool: Pool, plan_code: str) -> Optional[dict]:
    """Return one active plan by its trusted server-side code."""

    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT *
            FROM subscription_plans
            WHERE code = $1 AND active = TRUE
            """,
            plan_code,
        )
    return _normalize_json_columns(dict(row), "features") if row else None


async def get_plan_by_stripe_ids(
    connection: Connection,
    *,
    stripe_product_id: Optional[str],
    stripe_price_id: Optional[str],
) -> Optional[dict]:
    """Resolve a Stripe subscription item to a local paid plan."""

    row = await connection.fetchrow(
        """
        SELECT *
        FROM subscription_plans
        WHERE active = TRUE
          AND code <> 'free'
          AND (
              ($1::TEXT IS NOT NULL AND stripe_product_id = $1)
              OR ($2::TEXT IS NOT NULL AND stripe_price_id = $2)
          )
        ORDER BY
            CASE WHEN stripe_product_id = $1 THEN 0 ELSE 1 END
        LIMIT 1
        """,
        stripe_product_id,
        stripe_price_id,
    )
    return _normalize_json_columns(dict(row), "features") if row else None


async def get_user_billing(pool: Pool, user_id: str) -> Optional[dict]:
    """Return the user's current billing record."""

    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            "SELECT * FROM user_billing WHERE user_id = $1",
            user_id,
        )
    return dict(row) if row else None


async def get_user_billing_summary(pool: Pool, user_id: str) -> Optional[dict]:
    """Return billing, plan, and current-window usage in one database query."""

    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            SELECT
                billing.user_id,
                billing.plan_code,
                billing.entitlement_status,
                billing.entitlement_source,
                billing.promo_redemption_id,
                billing.stripe_customer_id,
                billing.stripe_subscription_id,
                billing.stripe_subscription_status,
                billing.current_period_start,
                billing.current_period_end,
                billing.access_started_at,
                billing.access_until AS access_expires_at,
                billing.cancel_at_period_end,
                billing.canceled_at,
                plans.name AS plan_name,
                plans.price_cents,
                plans.currency,
                plans.billing_interval,
                plans.usage_limit_usd,
                plans.window_hours,
                plans.features,
                limits.window_start,
                limits.plan_expires_at,
                limits.window_start
                    + (plans.window_hours * INTERVAL '1 hour') AS reset_at,
                limits.window_spent_usd,
                limits.limit_usd,
                GREATEST(
                    limits.limit_usd - limits.window_spent_usd,
                    0
                ) AS remaining_usd,
                limits.status AS rate_limit_status,
                limits.block_reason
            FROM user_billing AS billing
            JOIN subscription_plans AS plans
              ON plans.code = billing.plan_code
            LEFT JOIN rate_limits AS limits
              ON limits.user_id = billing.user_id
            WHERE billing.user_id = $1
            """,
            user_id,
        )
    return _normalize_json_columns(dict(row), "features") if row else None


async def save_stripe_customer_id(
    pool: Pool,
    *,
    user_id: str,
    stripe_customer_id: str,
) -> None:
    """Persist the Stripe Customer created for a user."""

    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE user_billing
            SET stripe_customer_id = $2, updated_at = NOW()
            WHERE user_id = $1
            """,
            user_id,
            stripe_customer_id,
        )


async def store_webhook_event(
    pool: Pool,
    *,
    stripe_event_id: str,
    event_type: str,
    stripe_object_id: Optional[str],
    payload: dict,
    stripe_created_at: datetime,
) -> str:
    """Durably store a Stripe event and return its current processing status."""

    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            INSERT INTO stripe_webhook_events (
                stripe_event_id,
                event_type,
                stripe_object_id,
                payload,
                stripe_created_at
            )
            VALUES ($1, $2, $3, $4::JSONB, $5)
            ON CONFLICT (stripe_event_id) DO UPDATE
            SET updated_at = stripe_webhook_events.updated_at
            RETURNING processing_status
            """,
            stripe_event_id,
            event_type,
            stripe_object_id,
            json.dumps(payload),
            stripe_created_at,
        )
    return str(row["processing_status"])


async def claim_webhook_event(pool: Pool, stripe_event_id: str) -> bool:
    """Atomically claim a pending/failed event for processing.

    A processing claim older than five minutes is considered abandoned and can
    be reclaimed after a worker crash.
    """

    stale_before = datetime.now(timezone.utc) - timedelta(minutes=5)
    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            """
            UPDATE stripe_webhook_events
            SET
                processing_status = 'processing',
                processing_started_at = NOW(),
                attempt_count = attempt_count + 1,
                last_error = NULL,
                updated_at = NOW()
            WHERE stripe_event_id = $1
              AND (
                  processing_status IN ('pending', 'failed')
                  OR (
                      processing_status = 'processing'
                      AND processing_started_at < $2
                  )
              )
            RETURNING stripe_event_id
            """,
            stripe_event_id,
            stale_before,
        )
    return row is not None


async def mark_webhook_processed(pool: Pool, stripe_event_id: str) -> None:
    """Mark a claimed webhook event as successfully processed."""

    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE stripe_webhook_events
            SET
                processing_status = 'processed',
                processed_at = NOW(),
                updated_at = NOW()
            WHERE stripe_event_id = $1
            """,
            stripe_event_id,
        )


async def mark_webhook_failed(
    pool: Pool,
    stripe_event_id: str,
    error: str,
) -> None:
    """Save a bounded webhook failure message so Stripe retries are useful."""

    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE stripe_webhook_events
            SET
                processing_status = 'failed',
                last_error = $2,
                updated_at = NOW()
            WHERE stripe_event_id = $1
            """,
            stripe_event_id,
            error[:2000],
        )


async def link_checkout_session(
    pool: Pool,
    *,
    user_id: str,
    stripe_customer_id: Optional[str],
    stripe_subscription_id: Optional[str],
) -> None:
    """Link Checkout objects without granting paid access prematurely."""

    async with pool.acquire() as connection:
        await connection.execute(
            """
            UPDATE user_billing
            SET
                stripe_customer_id = COALESCE($2, stripe_customer_id),
                stripe_subscription_id = COALESCE($3, stripe_subscription_id),
                updated_at = NOW()
            WHERE user_id = $1
            """,
            user_id,
            stripe_customer_id,
            stripe_subscription_id,
        )


async def find_user_id_for_stripe_customer(
    pool: Pool,
    stripe_customer_id: Optional[str],
) -> Optional[str]:
    """Find the internal user associated with a Stripe Customer."""

    if not stripe_customer_id:
        return None
    async with pool.acquire() as connection:
        return await connection.fetchval(
            """
            SELECT user_id
            FROM user_billing
            WHERE stripe_customer_id = $1
            """,
            stripe_customer_id,
        )


async def apply_subscription_snapshot(
    pool: Pool,
    *,
    user_id: str,
    stripe_event_id: str,
    stripe_event_created_at: datetime,
    effective_plan_code: str,
    entitlement_status: str,
    stripe_customer_id: Optional[str],
    stripe_subscription_id: str,
    stripe_subscription_status: str,
    stripe_product_id: Optional[str],
    stripe_price_id: Optional[str],
    current_period_start: Optional[datetime],
    current_period_end: Optional[datetime],
    access_started_at: datetime,
    access_until: Optional[datetime],
    cancel_at_period_end: bool,
    canceled_at: Optional[datetime],
) -> bool:
    """Apply a Stripe subscription snapshot to every local entitlement cache.

    The billing row, user profile tier, and rate-limit amount change in one
    transaction.  Older out-of-order subscription events are ignored.
    """

    async with pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                user_id,
            )

            billing = await connection.fetchrow(
                """
                SELECT last_stripe_event_created_at, promo_redemption_id
                FROM user_billing
                WHERE user_id = $1
                FOR UPDATE
                """,
                user_id,
            )
            if billing is None:
                return False

            last_event_at = billing["last_stripe_event_created_at"]
            if last_event_at and last_event_at > stripe_event_created_at:
                return False

            plan = await connection.fetchrow(
                """
                SELECT code, usage_limit_usd
                FROM subscription_plans
                WHERE code = $1 AND active = TRUE
                """,
                effective_plan_code,
            )
            if plan is None:
                raise ValueError(f"Unknown effective plan: {effective_plan_code}")

            if billing["promo_redemption_id"] is not None:
                await connection.execute(
                    """
                    UPDATE promo_redemptions
                    SET status = 'revoked', revoked_at = NOW()
                    WHERE id = $1 AND status = 'active'
                    """,
                    billing["promo_redemption_id"],
                )

            entitlement_source = (
                "payment" if effective_plan_code != "free" else "free"
            )

            await connection.execute(
                """
                UPDATE user_billing
                SET
                    plan_code = $2,
                    entitlement_status = $3,
                    entitlement_source = $17,
                    promo_redemption_id = NULL,
                    stripe_customer_id = COALESCE($4, stripe_customer_id),
                    stripe_subscription_id = $5,
                    stripe_subscription_status = $6,
                    stripe_product_id = $7,
                    stripe_price_id = $8,
                    current_period_start = $9,
                    current_period_end = $10,
                    access_started_at = $11,
                    access_until = $12,
                    cancel_at_period_end = $13,
                    canceled_at = $14,
                    last_stripe_event_id = $15,
                    last_stripe_event_created_at = $16,
                    updated_at = NOW()
                WHERE user_id = $1
                """,
                user_id,
                effective_plan_code,
                entitlement_status,
                stripe_customer_id,
                stripe_subscription_id,
                stripe_subscription_status,
                stripe_product_id,
                stripe_price_id,
                current_period_start,
                current_period_end,
                access_started_at,
                access_until,
                cancel_at_period_end,
                canceled_at,
                stripe_event_id,
                stripe_event_created_at,
                entitlement_source,
            )

            await connection.execute(
                """
                UPDATE users
                SET plan_code = $2, plan_updated_at = NOW(), updated_at = NOW()
                WHERE id = $1
                """,
                user_id,
                effective_plan_code,
            )

            usage_limit = Decimal(str(plan["usage_limit_usd"]))
            rate_limit_row = await connection.fetchrow(
                """
                SELECT window_spent_usd
                FROM rate_limits
                WHERE user_id = $1
                FOR UPDATE
                """,
                user_id,
            )
            if rate_limit_row:
                spent = Decimal(str(rate_limit_row["window_spent_usd"] or 0))
                is_blocked = spent >= usage_limit
                await connection.execute(
                    """
                    UPDATE rate_limits
                    SET
                        plan_code = $2,
                        limit_usd = $3,
                        plan_expires_at = NULL,
                        status = $4,
                        block_reason = $5,
                        updated_at = NOW()
                    WHERE user_id = $1
                    """,
                    user_id,
                    effective_plan_code,
                    usage_limit,
                    "blocked" if is_blocked else "active",
                    "usage_limit" if is_blocked else None,
                )
            else:
                await connection.execute(
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
                    VALUES ($1, $2, $3, NOW(), 0, 0, 0, $4, 'active', NULL, NOW())
                    """,
                    str(uuid.uuid4()),
                    user_id,
                    effective_plan_code,
                    usage_limit,
                )
    return True


def _clean_env_value(name: str) -> Optional[str]:
    """Return a non-placeholder environment value or ``None``."""

    value = (os.getenv(name) or "").strip().strip('"').strip("'")
    if not value or value.upper().startswith("YOUR "):
        return None
    return value


def _normalize_json_columns(row: dict, *column_names: str) -> dict:
    """Decode JSON strings returned by asyncpg's default JSON codec."""

    for column_name in column_names:
        value: Any = row.get(column_name)
        if isinstance(value, str):
            try:
                row[column_name] = json.loads(value)
            except json.JSONDecodeError:
                row[column_name] = {}
    return row
