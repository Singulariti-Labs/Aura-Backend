import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.DB.Queries.promotion import (
    expire_promotion_if_due,
    redeem_promo_code_transaction,
)


class _AsyncContext:
    def __init__(self, value=None):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _RedemptionConnection:
    def __init__(self, now):
        self.now = now
        self.statements = []

    def transaction(self):
        return _AsyncContext()

    async def execute(self, sql, *args):
        self.statements.append((sql, args))
        return "UPDATE 1"

    async def fetchrow(self, sql, *args):
        if "FROM user_billing" in sql:
            return {
                "plan_code": "free",
                "entitlement_source": "free",
                "access_until": None,
                "promo_redemption_id": None,
            }
        if "FROM promo_codes AS promo" in sql:
            return {
                "id": "promo-1",
                "plan_code": "pro",
                "active": True,
                "valid_from": self.now - timedelta(days=1),
                "valid_until": self.now + timedelta(days=1),
                "access_duration_days": 30,
                "max_redemptions": 1,
                "redemption_count": 0,
                "usage_limit_usd": Decimal("10"),
            }
        if "SELECT window_spent_usd" in sql:
            return {"window_spent_usd": Decimal("2.25")}
        raise AssertionError(f"Unexpected fetchrow SQL: {sql}")

    async def fetchval(self, sql, *args):
        return None


class _Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _AsyncContext(self.connection)


class _ExpiryConnection:
    def __init__(self, now):
        self.now = now
        self.statements = []

    async def execute(self, sql, *args):
        self.statements.append((sql, args))
        return "UPDATE 1"

    async def fetchrow(self, sql, *args):
        if "FROM user_billing" in sql:
            return {
                "entitlement_source": "promo",
                "access_until": self.now - timedelta(seconds=1),
                "promo_redemption_id": "redemption-1",
            }
        if "FROM subscription_plans" in sql:
            return {"code": "free", "usage_limit_usd": Decimal("3")}
        raise AssertionError(f"Unexpected fetchrow SQL: {sql}")


class PromotionQueryTests(unittest.IsolatedAsyncioTestCase):
    """Protect atomic redemption and expiration decisions."""

    async def test_redemption_preserves_window_spend(self):
        now = datetime(2026, 8, 20, tzinfo=timezone.utc)
        connection = _RedemptionConnection(now)

        result = await redeem_promo_code_transaction(
            _Pool(connection),
            user_id="user-1",
            code_hash="a" * 64,
            now=now,
        )

        self.assertEqual(result["outcome"], "redeemed")
        self.assertEqual(result["plan_code"], "pro")
        self.assertEqual(result["window_spent_usd"], Decimal("2.25"))
        self.assertEqual(result["usage_limit_usd"], Decimal("10"))
        self.assertEqual(result["access_started_at"], now)

        billing_updates = [
            (sql, args)
            for sql, args in connection.statements
            if "UPDATE user_billing" in sql
        ]
        self.assertEqual(len(billing_updates), 1)
        self.assertIn("access_started_at", billing_updates[0][0])

    async def test_expired_promotion_returns_to_free_and_can_block(self):
        now = datetime(2026, 8, 20, tzinfo=timezone.utc)
        connection = _ExpiryConnection(now)

        result = await expire_promotion_if_due(
            connection,
            user_id="user-1",
            now=now,
            current_spent_usd=Decimal("4"),
        )

        self.assertEqual(result["plan_code"], "free")
        self.assertEqual(result["limit_usd"], Decimal("3"))
        self.assertEqual(result["status"], "blocked")
        billing_updates = [
            (sql, args)
            for sql, args in connection.statements
            if "UPDATE user_billing" in sql
        ]
        self.assertEqual(len(billing_updates), 1)
        self.assertIn("access_started_at", billing_updates[0][0])


if __name__ == "__main__":
    unittest.main()
