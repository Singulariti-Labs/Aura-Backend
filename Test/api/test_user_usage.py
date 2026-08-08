import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from app.RateLimit.usage_service import fetch_user_usage


class UserUsageServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetches_both_usage_rows_concurrently(self):
        both_started = asyncio.Event()
        started_count = 0

        async def wait_for_other_query(result):
            nonlocal started_count
            started_count += 1
            if started_count == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=0.5)
            return result

        async def overall_query(pool, user_id):
            return await wait_for_other_query(overall_row)

        async def rate_limit_query(pool, user_id):
            return await wait_for_other_query(rate_limit_row)

        window_start = datetime(2026, 8, 3, 6, 30, tzinfo=timezone.utc)
        overall_row = {
            "total_input_tokens": 120,
            "total_output_tokens": 30,
            "total_spent_usd": Decimal("1.25"),
        }
        rate_limit_row = {
            "window_start": window_start,
            "window_input_tokens": 20,
            "window_output_tokens": 5,
            "window_spent_usd": Decimal("0.75"),
            "limit_usd": Decimal("3"),
            "status": "active",
        }

        with (
            patch(
                "app.RateLimit.usage_service.get_user_token_usage",
                side_effect=overall_query,
            ),
            patch(
                "app.RateLimit.usage_service.get_user_rate_limit",
                side_effect=rate_limit_query,
            ),
        ):
            result = await fetch_user_usage(object(), "user-1")

        self.assertEqual(
            result["overall_usage"],
            {
                "input_tokens": 120,
                "output_tokens": 30,
                "total_tokens": 150,
                "spent_usd": Decimal("1.25"),
            },
        )
        self.assertEqual(result["rate_limit"]["total_tokens"], 25)
        self.assertEqual(result["rate_limit"]["remaining_usd"], Decimal("2.25"))
        self.assertEqual(
            result["rate_limit"]["reset_at"],
            window_start + timedelta(hours=12),
        )

    async def test_returns_stable_defaults_when_usage_rows_do_not_exist(self):
        with (
            patch(
                "app.RateLimit.usage_service.get_user_token_usage",
                return_value=None,
            ),
            patch(
                "app.RateLimit.usage_service.get_user_rate_limit",
                return_value=None,
            ),
        ):
            result = await fetch_user_usage(object(), "new-user")

        self.assertEqual(result["overall_usage"]["total_tokens"], 0)
        self.assertEqual(result["overall_usage"]["spent_usd"], Decimal("0"))
        self.assertIsNone(result["rate_limit"]["window_start"])
        self.assertIsNone(result["rate_limit"]["reset_at"])
        self.assertEqual(result["rate_limit"]["remaining_usd"], Decimal("3"))
        self.assertEqual(result["rate_limit"]["status"], "active")
