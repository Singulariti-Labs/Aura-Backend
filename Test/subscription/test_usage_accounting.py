import unittest
from decimal import Decimal

from app.RateLimit.rate_limit_service import _build_usage_event


class UsageAccountingTests(unittest.TestCase):
    """Protect subscription accounting rules that affect customer allowance."""

    def test_platform_call_consumes_reported_cost(self):
        event = _build_usage_event(
            user_id="user-1",
            usage={"input": 100, "output": 20, "cost": 0.25},
            details={
                "provider": "openai",
                "model_name": "gpt-5",
                "credential_source": "platform",
            },
        )

        self.assertIsNotNone(event)
        self.assertEqual(event.spent_usd, Decimal("0.25"))
        self.assertEqual(event.credential_source, "platform")

    def test_custom_key_is_audited_without_consuming_platform_allowance(self):
        event = _build_usage_event(
            user_id="user-1",
            usage={"input": 100, "output": 20, "cost": 0.25},
            details={
                "provider": "openai",
                "model_name": "gpt-5",
                "credential_source": "custom",
            },
        )

        self.assertIsNotNone(event)
        self.assertEqual(event.spent_usd, Decimal("0"))
        self.assertEqual(event.credential_source, "custom")


if __name__ == "__main__":
    unittest.main()
