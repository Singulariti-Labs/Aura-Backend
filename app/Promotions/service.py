"""Business-facing service for promotion creation and redemption."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from asyncpg import Pool, UniqueViolationError

from app.DB.Queries.promotion import (
    create_promo_code,
    redeem_promo_code_transaction,
)
from app.Promotions.code_security import (
    generate_promo_code,
    get_promo_code_pepper,
    hash_promo_code,
    mask_promo_code,
    normalize_promo_code,
)


class InvalidPromotionError(RuntimeError):
    """Raised for invalid, disabled, expired, exhausted, or reused codes."""


class PromotionConflictError(RuntimeError):
    """Raised when an existing entitlement has equal or higher authority."""


class PromotionUserNotFoundError(RuntimeError):
    """Raised when the authenticated user lacks initialized billing state."""


async def redeem_promotion(
    *,
    pool: Pool,
    user_id: str,
    code: str,
    pepper: Optional[str] = None,
    now: Optional[datetime] = None,
) -> dict:
    """Redeem a code and return a JSON-ready effective-plan response.

    Detailed validation failures intentionally share one public error so an
    attacker cannot use this endpoint to discover valid or exhausted codes.
    """

    try:
        code_hash = hash_promo_code(
            code,
            pepper=pepper or get_promo_code_pepper(),
        )
    except ValueError as exc:
        raise InvalidPromotionError("Invalid or expired promo code") from exc

    result = await redeem_promo_code_transaction(
        pool,
        user_id=user_id,
        code_hash=code_hash,
        now=now or datetime.now(timezone.utc),
    )
    outcome = result.get("outcome")
    if outcome == "invalid":
        raise InvalidPromotionError("Invalid or expired promo code")
    if outcome == "conflict":
        raise PromotionConflictError(
            "Your current plan cannot be replaced by this promo code"
        )
    if outcome == "user_missing":
        raise PromotionUserNotFoundError("User billing record is missing")
    if outcome != "redeemed":
        raise RuntimeError("Unexpected promotion redemption outcome")

    return {
        "plan_code": result["plan_code"],
        "previous_plan_code": result["previous_plan_code"],
        "entitlement_source": "promo",
        "access_started_at": result["access_started_at"],
        "access_expires_at": result["access_expires_at"],
        "usage_limit_usd": str(result["usage_limit_usd"]),
        "window_spent_usd": str(result["window_spent_usd"]),
        "message": f"{str(result['plan_code']).title()} plan activated",
    }


async def generate_and_store_promotion(
    *,
    pool: Pool,
    plan_code: str,
    access_duration_days: Optional[int],
    max_redemptions: Optional[int],
    valid_for_days: Optional[int],
    metadata: Optional[dict] = None,
    pepper: Optional[str] = None,
    now: Optional[datetime] = None,
    code: Optional[str] = None,
) -> tuple[str, dict]:
    """Create a secret code, store only its hash, and return it once.

    A caller-supplied code is used when present; otherwise a high-entropy code
    is generated. This function is intended for the internal CLI. The
    plaintext value must be delivered securely because it cannot be recovered
    from the database later.
    """

    if plan_code not in {"mini", "pro", "max"}:
        raise ValueError("plan_code must be mini, pro, or max")
    if access_duration_days is not None and access_duration_days <= 0:
        raise ValueError("access_duration_days must be positive")
    if max_redemptions is not None and max_redemptions <= 0:
        raise ValueError("max_redemptions must be positive")
    if valid_for_days is not None and valid_for_days <= 0:
        raise ValueError("valid_for_days must be positive")

    generated_at = now or datetime.now(timezone.utc)
    plaintext_code = generate_promo_code(plan_code) if code is None else code.strip()
    if len(plaintext_code) > 128:
        raise ValueError("Promo code must not exceed 128 characters")
    if len(normalize_promo_code(plaintext_code)) < 16:
        raise ValueError(
            "Promo code must contain at least 16 letters or numbers"
        )

    try:
        record = await create_promo_code(
            pool,
            code_hash=hash_promo_code(
                plaintext_code,
                pepper=pepper or get_promo_code_pepper(),
            ),
            code_hint=mask_promo_code(plaintext_code),
            plan_code=plan_code,
            valid_from=generated_at,
            valid_until=(
                generated_at + timedelta(days=valid_for_days)
                if valid_for_days is not None
                else None
            ),
            access_duration_days=access_duration_days,
            max_redemptions=max_redemptions,
            metadata=metadata,
        )
    except UniqueViolationError as exc:
        message = (
            "Promo code already exists; choose a different --code value"
            if code is not None
            else "Generated promo code collided; run the command again"
        )
        raise ValueError(message) from exc
    return plaintext_code, record
