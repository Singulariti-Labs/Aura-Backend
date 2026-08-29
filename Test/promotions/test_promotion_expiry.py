import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from app.RateLimit.rate_limit import check_rate_limit_for_request


class _AsyncContext:
    def __init__(self, value=None):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _RateLimitConnection:
    def __init__(self):
        now = datetime.now(timezone.utc)
        self.row = {
            "id": "rate-1",
            "user_id": "user-1",
            "window_start": now - timedelta(hours=1),
            "window_input_tokens": 100,
            "window_output_tokens": 25,
            "window_spent_usd": Decimal("4"),
            "limit_usd": Decimal("10"),
            "plan_code": "pro",
            "plan_expires_at": now - timedelta(seconds=1),
            "status": "active",
            "block_reason": None,
            "updated_at": now,
        }
        self.statements = []

    def transaction(self):
        return _AsyncContext()

    async def fetchrow(self, sql, *args):
        return self.row

    async def execute(self, sql, *args):
        self.statements.append((sql, args))
        return "UPDATE 1"


class _Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _AsyncContext(self.connection)


class PromotionExpiryRateLimitTests(unittest.IsolatedAsyncioTestCase):
    """Ensure expiry is enforced inside the existing request-time query path."""

    async def test_expired_pro_plan_uses_free_limit_for_current_decision(self):
        connection = _RateLimitConnection()
        expire = AsyncMock(
            return_value={
                "plan_code": "free",
                "limit_usd": Decimal("3"),
                "status": "blocked",
                "block_reason": "usage_limit",
            }
        )
        with patch(
            "app.RateLimit.rate_limit.expire_promotion_if_due",
            expire,
        ):
            decision = await check_rate_limit_for_request(
                pool=_Pool(connection),
                user_id="user-1",
                timezone_name="UTC",
            )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.plan_code, "free")
        self.assertEqual(decision.limit_usd, Decimal("3"))
        expire.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
