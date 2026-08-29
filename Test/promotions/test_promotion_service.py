import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from asyncpg import UniqueViolationError

from app.Promotions.code_security import hash_promo_code
from app.Promotions.service import (
    InvalidPromotionError,
    PromotionConflictError,
    generate_and_store_promotion,
    redeem_promotion,
)


class PromotionServiceTests(unittest.IsolatedAsyncioTestCase):
    """Test public promotion behavior independently from SQL details."""

    async def test_successful_redemption_returns_effective_plan(self):
        now = datetime(2026, 8, 20, tzinfo=timezone.utc)
        with patch(
            "app.Promotions.service.redeem_promo_code_transaction",
            new_callable=AsyncMock,
            return_value={
                "outcome": "redeemed",
                "plan_code": "pro",
                "previous_plan_code": "free",
                "access_started_at": now,
                "access_expires_at": now,
                "usage_limit_usd": 10,
                "window_spent_usd": 2,
            },
        ):
            result = await redeem_promotion(
                pool=object(),
                user_id="user-1",
                code="PRO-ABCDE-FGHIJ-23456",
                pepper="p" * 32,
                now=now,
            )

        self.assertEqual(result["plan_code"], "pro")
        self.assertEqual(result["entitlement_source"], "promo")
        self.assertEqual(result["access_started_at"], now)
        self.assertEqual(result["usage_limit_usd"], "10")

    async def test_invalid_database_outcome_is_generic(self):
        with patch(
            "app.Promotions.service.redeem_promo_code_transaction",
            new_callable=AsyncMock,
            return_value={"outcome": "invalid"},
        ):
            with self.assertRaisesRegex(
                InvalidPromotionError,
                "Invalid or expired promo code",
            ):
                await redeem_promotion(
                    pool=object(),
                    user_id="user-1",
                    code="PRO-ABCDE-FGHIJ-23456",
                    pepper="p" * 32,
                )

    async def test_existing_higher_entitlement_returns_conflict(self):
        with patch(
            "app.Promotions.service.redeem_promo_code_transaction",
            new_callable=AsyncMock,
            return_value={"outcome": "conflict"},
        ):
            with self.assertRaises(PromotionConflictError):
                await redeem_promotion(
                    pool=object(),
                    user_id="user-1",
                    code="MINI-ABCDE-FGHIJ-23456",
                    pepper="p" * 32,
                )

    async def test_generator_stores_hash_but_returns_plaintext_once(self):
        store = AsyncMock(
            return_value={
                "plan_code": "mini",
                "access_duration_days": 30,
                "max_redemptions": 1,
                "valid_until": None,
            }
        )
        with patch("app.Promotions.service.create_promo_code", store):
            code, _ = await generate_and_store_promotion(
                pool=object(),
                plan_code="mini",
                access_duration_days=30,
                max_redemptions=1,
                valid_for_days=None,
                pepper="p" * 32,
            )

        self.assertTrue(code.startswith("MINI-"))
        self.assertNotEqual(store.await_args.kwargs["code_hash"], code)
        self.assertNotIn(code, store.await_args.kwargs["code_hint"])

    async def test_custom_code_is_hashed_and_returned_once(self):
        store = AsyncMock(
            return_value={
                "plan_code": "max",
                "access_duration_days": 60,
                "max_redemptions": 5,
                "valid_until": None,
            }
        )
        custom_code = "AURA-FOUNDERS-2026-ACCESS"
        pepper = "p" * 32
        with patch("app.Promotions.service.create_promo_code", store):
            returned_code, _ = await generate_and_store_promotion(
                pool=object(),
                plan_code="max",
                access_duration_days=60,
                max_redemptions=5,
                valid_for_days=None,
                pepper=pepper,
                code=f"  {custom_code}  ",
            )

        self.assertEqual(returned_code, custom_code)
        self.assertEqual(
            store.await_args.kwargs["code_hash"],
            hash_promo_code(custom_code, pepper=pepper),
        )
        self.assertNotIn(custom_code, store.await_args.kwargs["code_hint"])

    async def test_custom_code_must_be_long_enough(self):
        store = AsyncMock()
        with (
            patch("app.Promotions.service.create_promo_code", store),
            self.assertRaisesRegex(ValueError, "at least 16"),
        ):
            await generate_and_store_promotion(
                pool=object(),
                plan_code="pro",
                access_duration_days=30,
                max_redemptions=1,
                valid_for_days=30,
                pepper="p" * 32,
                code="TOO-SHORT",
            )

        store.assert_not_awaited()

    async def test_duplicate_custom_code_has_clear_error(self):
        store = AsyncMock(side_effect=UniqueViolationError("duplicate"))
        with (
            patch("app.Promotions.service.create_promo_code", store),
            self.assertRaisesRegex(ValueError, "already exists"),
        ):
            await generate_and_store_promotion(
                pool=object(),
                plan_code="pro",
                access_duration_days=30,
                max_redemptions=1,
                valid_for_days=30,
                pepper="p" * 32,
                code="AURA-DUPLICATE-CODE-2026",
            )


if __name__ == "__main__":
    unittest.main()
