import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from app.Subscription.config import StripeSettings
from app.Subscription.service import (
    SubscriptionConflictError,
    _process_subscription_event,
    process_verified_webhook,
    reconcile_stale_paid_entitlement,
    start_checkout,
    try_reconcile_stale_paid_entitlement,
)


class _AcquireContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Pool:
    def acquire(self):
        return _AcquireContext()


def _subscription(status="active"):
    return {
        "id": "sub_123",
        "customer": "cus_123",
        "status": status,
        "cancel_at_period_end": False,
        "metadata": {"user_id": "user-1"},
        "items": {
            "data": [
                {
                    "current_period_start": 1787184000,
                    "current_period_end": 1789862400,
                    "price": {
                        "id": "price_pro",
                        "product": "prod_pro",
                    },
                }
            ]
        },
    }


class SubscriptionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_subscription_applies_paid_plan(self):
        applied = AsyncMock(return_value=True)
        with (
            patch(
                "app.Subscription.service.get_plan_by_stripe_ids",
                new_callable=AsyncMock,
                return_value={"code": "pro"},
            ),
            patch(
                "app.Subscription.service.apply_subscription_snapshot",
                applied,
            ),
        ):
            await _process_subscription_event(
                _Pool(),
                event_id="evt_1",
                event_type="customer.subscription.updated",
                event_created_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
                subscription=_subscription("active"),
            )

        self.assertEqual(applied.await_args.kwargs["effective_plan_code"], "pro")
        self.assertEqual(applied.await_args.kwargs["entitlement_status"], "active")
        self.assertEqual(applied.await_args.kwargs["stripe_price_id"], "price_pro")
        self.assertEqual(
            applied.await_args.kwargs["access_started_at"],
            applied.await_args.kwargs["current_period_start"],
        )
        self.assertIsNotNone(applied.await_args.kwargs["current_period_end"])

    async def test_past_due_subscription_falls_back_to_free(self):
        applied = AsyncMock(return_value=True)
        with (
            patch(
                "app.Subscription.service.get_plan_by_stripe_ids",
                new_callable=AsyncMock,
                return_value={"code": "mini"},
            ),
            patch(
                "app.Subscription.service.apply_subscription_snapshot",
                applied,
            ),
        ):
            await _process_subscription_event(
                _Pool(),
                event_id="evt_2",
                event_type="customer.subscription.updated",
                event_created_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
                subscription=_subscription("past_due"),
            )

        self.assertEqual(applied.await_args.kwargs["effective_plan_code"], "free")
        self.assertEqual(
            applied.await_args.kwargs["entitlement_status"],
            "restricted",
        )
        self.assertEqual(
            applied.await_args.kwargs["access_started_at"],
            datetime(2026, 8, 20, tzinfo=timezone.utc),
        )

    async def test_duplicate_webhook_is_not_processed_twice(self):
        event = {
            "id": "evt_duplicate",
            "type": "invoice.paid",
            "created": 1787184000,
            "data": {"object": {"id": "in_123"}},
        }
        with (
            patch(
                "app.Subscription.service.store_webhook_event",
                new_callable=AsyncMock,
                return_value="processed",
            ),
            patch(
                "app.Subscription.service.claim_webhook_event",
                new_callable=AsyncMock,
            ) as claim,
        ):
            result = await process_verified_webhook(object(), event)

        self.assertEqual(result, "duplicate")
        claim.assert_not_awaited()


class SubscriptionReconciliationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 22, tzinfo=timezone.utc)
        self.settings = StripeSettings(
            secret_key="sk_test_example",
            webhook_secret="whsec_example",
            success_url="https://app.example.test/success",
            cancel_url="https://app.example.test/cancel",
            portal_return_url="https://app.example.test/billing",
        )

    def stale_billing(self, **overrides):
        billing = {
            "plan_code": "pro",
            "entitlement_source": "payment",
            "stripe_subscription_id": "sub_123",
            "access_until": self.now - timedelta(minutes=1),
            "current_period_end": self.now - timedelta(minutes=1),
        }
        billing.update(overrides)
        return billing

    async def test_missed_renewal_keeps_paid_plan_and_saves_new_period(self):
        applied = AsyncMock(return_value=True)
        with (
            patch(
                "app.Subscription.service.get_user_billing",
                new_callable=AsyncMock,
                return_value=self.stale_billing(),
            ),
            patch(
                "app.Subscription.service.stripe_retrieve_subscription",
                new_callable=AsyncMock,
                return_value=_subscription("active"),
            ) as retrieve,
            patch(
                "app.Subscription.service.get_plan_by_stripe_ids",
                new_callable=AsyncMock,
                return_value={"code": "pro"},
            ),
            patch("app.Subscription.service.apply_subscription_snapshot", applied),
        ):
            result = await reconcile_stale_paid_entitlement(
                pool=_Pool(),
                user_id="user-1",
                settings=self.settings,
                now=self.now,
            )

        self.assertEqual(result, "reconciled")
        retrieve.assert_awaited_once_with(
            settings=self.settings,
            stripe_subscription_id="sub_123",
        )
        self.assertEqual(applied.await_args.kwargs["effective_plan_code"], "pro")
        self.assertGreater(applied.await_args.kwargs["access_until"], self.now)

    async def test_missed_cancellation_downgrades_to_free(self):
        applied = AsyncMock(return_value=True)
        with (
            patch(
                "app.Subscription.service.get_user_billing",
                new_callable=AsyncMock,
                return_value=self.stale_billing(),
            ),
            patch(
                "app.Subscription.service.stripe_retrieve_subscription",
                new_callable=AsyncMock,
                return_value=_subscription("canceled"),
            ),
            patch(
                "app.Subscription.service.get_plan_by_stripe_ids",
                new_callable=AsyncMock,
                return_value={"code": "pro"},
            ),
            patch("app.Subscription.service.apply_subscription_snapshot", applied),
        ):
            result = await reconcile_stale_paid_entitlement(
                pool=_Pool(),
                user_id="user-1",
                settings=self.settings,
                now=self.now,
            )

        self.assertEqual(result, "reconciled")
        self.assertEqual(applied.await_args.kwargs["effective_plan_code"], "free")
        self.assertIsNone(applied.await_args.kwargs["access_until"])

    async def test_current_and_non_payment_accounts_do_not_call_stripe(self):
        retrieve = AsyncMock()
        unaffected_accounts = (
            self.stale_billing(entitlement_source="promo"),
            self.stale_billing(
                access_until=self.now + timedelta(days=1),
                current_period_end=self.now + timedelta(days=1),
            ),
            {"plan_code": "free", "entitlement_source": "free"},
        )

        for billing in unaffected_accounts:
            with (
                self.subTest(billing=billing),
                patch(
                    "app.Subscription.service.get_user_billing",
                    new_callable=AsyncMock,
                    return_value=billing,
                ),
                patch(
                    "app.Subscription.service.stripe_retrieve_subscription",
                    retrieve,
                ),
            ):
                result = await reconcile_stale_paid_entitlement(
                    pool=object(),
                    user_id="user-1",
                    settings=self.settings,
                    now=self.now,
                )
                self.assertEqual(result, "not_due")

        retrieve.assert_not_awaited()

    async def test_stripe_failure_does_not_break_login_or_account_reads(self):
        with (
            patch(
                "app.Subscription.service.get_user_billing",
                new_callable=AsyncMock,
                return_value=self.stale_billing(),
            ),
            patch(
                "app.Subscription.service.stripe_retrieve_subscription",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Stripe temporarily unavailable"),
            ),
            patch("app.Subscription.service.logger.exception") as log_exception,
        ):
            result = await try_reconcile_stale_paid_entitlement(
                pool=object(),
                user_id="user-1",
                settings=self.settings,
                now=self.now,
            )

        self.assertEqual(result, "failed")
        log_exception.assert_called_once()


class CheckoutTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = StripeSettings(
            secret_key="sk_test_example",
            webhook_secret="whsec_example",
            success_url="https://app.example.test/success",
            cancel_url="https://app.example.test/cancel",
            portal_return_url="https://app.example.test/billing",
        )
        self.user = {
            "id": "user-1",
            "email": "user@example.test",
            "name": "Example User",
        }

    async def test_checkout_uses_server_plan_price(self):
        with (
            patch(
                "app.Subscription.service.get_plan_by_code",
                new_callable=AsyncMock,
                return_value={
                    "code": "mini",
                    "stripe_price_id": "price_server_trusted",
                },
            ),
            patch(
                "app.Subscription.service.get_user_billing",
                new_callable=AsyncMock,
                return_value={
                    "stripe_customer_id": "cus_123",
                    "stripe_subscription_id": None,
                    "stripe_subscription_status": None,
                },
            ),
            patch(
                "app.Subscription.service.stripe_create_checkout_session",
                new_callable=AsyncMock,
                return_value={"session_id": "cs_123", "url": "https://checkout"},
            ) as create_session,
        ):
            result = await start_checkout(
                pool=object(),
                settings=self.settings,
                user=self.user,
                plan_code="mini",
                request_id="request-123",
            )

        self.assertEqual(result["session_id"], "cs_123")
        self.assertEqual(
            create_session.await_args.kwargs["stripe_price_id"],
            "price_server_trusted",
        )

    async def test_active_subscription_must_use_portal(self):
        with (
            patch(
                "app.Subscription.service.get_plan_by_code",
                new_callable=AsyncMock,
                return_value={"code": "pro", "stripe_price_id": "price_pro"},
            ),
            patch(
                "app.Subscription.service.get_user_billing",
                new_callable=AsyncMock,
                return_value={
                    "stripe_customer_id": "cus_123",
                    "stripe_subscription_id": "sub_123",
                    "stripe_subscription_status": "active",
                },
            ),
        ):
            with self.assertRaises(SubscriptionConflictError):
                await start_checkout(
                    pool=object(),
                    settings=self.settings,
                    user=self.user,
                    plan_code="pro",
                    request_id="request-123",
                )
