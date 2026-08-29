"""Application service for Checkout, Portal, and Stripe webhook lifecycles."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from asyncpg import Pool

from app.DB.Queries.subscription import (
    apply_subscription_snapshot,
    claim_webhook_event,
    find_user_id_for_stripe_customer,
    get_plan_by_code,
    get_plan_by_stripe_ids,
    get_user_billing,
    link_checkout_session,
    mark_webhook_failed,
    mark_webhook_processed,
    save_stripe_customer_id,
    store_webhook_event,
)
from app.Subscription.config import StripeSettings
from app.Subscription.stripe_gateway import (
    create_checkout_session as stripe_create_checkout_session,
    create_customer as stripe_create_customer,
    create_portal_session as stripe_create_portal_session,
    retrieve_subscription as stripe_retrieve_subscription,
)


logger = logging.getLogger(__name__)
PAID_ACCESS_STATUSES = {"active", "trialing"}
INACTIVE_ACCESS_STATUSES = {
    "canceled",
    "incomplete",
    "incomplete_expired",
    "paused",
    "past_due",
    "unpaid",
}
SUBSCRIPTION_EVENT_TYPES = {
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
}


class SubscriptionConflictError(RuntimeError):
    """Raised when Checkout would create a second active subscription."""


class SubscriptionNotReadyError(RuntimeError):
    """Raised when required local Stripe catalog/customer data is unavailable."""


async def start_checkout(
    *,
    pool: Pool,
    settings: StripeSettings,
    user: dict,
    plan_code: str,
    request_id: str,
) -> dict:
    """Create or reuse a Customer and start trusted server-side Checkout."""

    plan = await get_plan_by_code(pool, plan_code)
    if not plan or plan_code == "free":
        raise SubscriptionNotReadyError("The selected paid plan is unavailable")

    stripe_price_id = plan.get("stripe_price_id")
    if not stripe_price_id:
        raise SubscriptionNotReadyError(
            f"Stripe Price ID is not configured for the {plan_code} plan"
        )

    await reconcile_stale_paid_entitlement(
        pool=pool,
        user_id=str(user["id"]),
        settings=settings,
    )
    billing = await get_user_billing(pool, str(user["id"]))
    if billing is None:
        raise SubscriptionNotReadyError("The user's billing record is missing")

    current_status = str(billing.get("stripe_subscription_status") or "")
    if billing.get("stripe_subscription_id") and current_status in {
        "active",
        "past_due",
        "trialing",
    }:
        raise SubscriptionConflictError(
            "An existing subscription must be managed through the billing portal"
        )

    customer_id = billing.get("stripe_customer_id")
    if not customer_id:
        customer_id = await stripe_create_customer(
            settings=settings,
            user_id=str(user["id"]),
            email=str(user.get("email") or ""),
            name=str(user.get("name") or ""),
        )
        await save_stripe_customer_id(
            pool,
            user_id=str(user["id"]),
            stripe_customer_id=customer_id,
        )

    return await stripe_create_checkout_session(
        settings=settings,
        user_id=str(user["id"]),
        stripe_customer_id=str(customer_id),
        plan_code=plan_code,
        stripe_price_id=str(stripe_price_id),
        request_id=request_id,
    )


async def start_customer_portal(
    *,
    pool: Pool,
    settings: StripeSettings,
    user_id: str,
) -> dict:
    """Create a Stripe Portal link for an existing Stripe Customer."""

    billing = await get_user_billing(pool, user_id)
    if not billing or not billing.get("stripe_customer_id"):
        raise SubscriptionNotReadyError(
            "No Stripe customer exists for this account yet"
        )
    return await stripe_create_portal_session(
        settings=settings,
        stripe_customer_id=str(billing["stripe_customer_id"]),
    )


async def process_verified_webhook(pool: Pool, event: dict) -> str:
    """Store and idempotently process one signature-verified Stripe event."""

    event_id = str(event.get("id") or "")
    event_type = str(event.get("type") or "")
    data_object = _mapping(event.get("data")).get("object")
    stripe_object = _mapping(data_object)
    object_id = _string_or_none(stripe_object.get("id"))
    event_created_at = _timestamp(event.get("created")) or datetime.now(timezone.utc)

    if not event_id or not event_type:
        raise ValueError("Stripe event is missing id or type")

    status = await store_webhook_event(
        pool,
        stripe_event_id=event_id,
        event_type=event_type,
        stripe_object_id=object_id,
        payload=event,
        stripe_created_at=event_created_at,
    )
    if status == "processed":
        return "duplicate"

    if not await claim_webhook_event(pool, event_id):
        return "already_processing"

    try:
        if event_type == "checkout.session.completed":
            await _process_checkout_completed(pool, stripe_object)
        elif event_type in SUBSCRIPTION_EVENT_TYPES:
            await _process_subscription_event(
                pool,
                event_id=event_id,
                event_type=event_type,
                event_created_at=event_created_at,
                subscription=stripe_object,
            )
        # Invoice events are retained for audit. Subscription created/updated/
        # deleted events remain the sole source for entitlement transitions.
        await mark_webhook_processed(pool, event_id)
        return "processed"
    except Exception as exc:
        logger.exception("Stripe webhook processing failed for event %s", event_id)
        await mark_webhook_failed(pool, event_id, str(exc) or exc.__class__.__name__)
        raise


async def _process_checkout_completed(pool: Pool, session: dict) -> None:
    """Link Checkout IDs but wait for subscription state before granting access."""

    metadata = _mapping(session.get("metadata"))
    user_id = _string_or_none(session.get("client_reference_id")) or _string_or_none(
        metadata.get("user_id")
    )
    if not user_id:
        raise ValueError("Checkout Session is missing the internal user id")

    await link_checkout_session(
        pool,
        user_id=user_id,
        stripe_customer_id=_object_id(session.get("customer")),
        stripe_subscription_id=_object_id(session.get("subscription")),
    )


async def _process_subscription_event(
    pool: Pool,
    *,
    event_id: str,
    event_type: str,
    event_created_at: datetime,
    subscription: dict,
    known_user_id: Optional[str] = None,
) -> bool:
    """Map a Stripe subscription snapshot to local plan and rate-limit state."""

    subscription_id = _string_or_none(subscription.get("id"))
    if not subscription_id:
        raise ValueError("Subscription event is missing subscription id")

    customer_id = _object_id(subscription.get("customer"))
    metadata = _mapping(subscription.get("metadata"))
    user_id = known_user_id or _string_or_none(metadata.get("user_id"))
    if not user_id:
        user_id = await find_user_id_for_stripe_customer(pool, customer_id)
    if not user_id:
        raise ValueError("Unable to map Stripe subscription to an internal user")

    item = _first_subscription_item(subscription)
    price = _mapping(item.get("price"))
    stripe_price_id = _object_id(item.get("price"))
    stripe_product_id = _object_id(price.get("product"))
    status = str(subscription.get("status") or "canceled")
    if event_type == "customer.subscription.deleted":
        status = "canceled"

    current_period_start = _timestamp(
        subscription.get("current_period_start")
        or item.get("current_period_start")
    )
    current_period_end = _timestamp(
        subscription.get("current_period_end")
        or item.get("current_period_end")
    )

    async with pool.acquire() as connection:
        paid_plan = await get_plan_by_stripe_ids(
            connection,
            stripe_product_id=stripe_product_id,
            stripe_price_id=stripe_price_id,
        )

    if status in PAID_ACCESS_STATUSES and paid_plan:
        effective_plan_code = str(paid_plan["code"])
        entitlement_status = "active"
        access_started_at = current_period_start or event_created_at
        access_until = current_period_end
    else:
        effective_plan_code = "free"
        entitlement_status = "restricted" if status == "past_due" else "active"
        access_started_at = event_created_at
        access_until = None
        if status in PAID_ACCESS_STATUSES and not paid_plan:
            logger.error(
                "Active Stripe subscription %s uses an unknown product/price",
                subscription_id,
            )

    if status not in PAID_ACCESS_STATUSES | INACTIVE_ACCESS_STATUSES:
        logger.warning("Unhandled Stripe subscription status %s", status)

    return await apply_subscription_snapshot(
        pool,
        user_id=user_id,
        stripe_event_id=event_id,
        stripe_event_created_at=event_created_at,
        effective_plan_code=effective_plan_code,
        entitlement_status=entitlement_status,
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription_id,
        stripe_subscription_status=status,
        stripe_product_id=stripe_product_id,
        stripe_price_id=stripe_price_id,
        current_period_start=current_period_start,
        current_period_end=current_period_end,
        access_started_at=access_started_at,
        access_until=access_until,
        cancel_at_period_end=bool(subscription.get("cancel_at_period_end", False)),
        canceled_at=_timestamp(subscription.get("canceled_at")),
    )


async def reconcile_stale_paid_entitlement(
    *,
    pool: Pool,
    user_id: str,
    settings: Optional[StripeSettings] = None,
    now: Optional[datetime] = None,
) -> str:
    """Reconcile an overdue paid entitlement directly with Stripe.

    Webhooks remain the primary synchronization mechanism. This fallback only
    calls Stripe when a payment-backed entitlement has passed its locally
    cached access boundary, which avoids adding Stripe latency to normal
    logins and account requests.
    """

    billing = await get_user_billing(pool, user_id)
    if not billing or billing.get("entitlement_source") != "payment":
        return "not_due"

    subscription_id = _string_or_none(billing.get("stripe_subscription_id"))
    access_until = _timestamp(
        billing.get("access_until") or billing.get("current_period_end")
    )
    checked_at = now or datetime.now(timezone.utc)
    if not subscription_id or access_until is None or access_until > checked_at:
        return "not_due"

    current_settings = settings or StripeSettings.from_env()
    subscription = await stripe_retrieve_subscription(
        settings=current_settings,
        stripe_subscription_id=subscription_id,
    )
    applied = await _process_subscription_event(
        pool,
        event_id=f"reconcile:{subscription_id}:{int(checked_at.timestamp())}",
        event_type="customer.subscription.updated",
        event_created_at=checked_at,
        subscription=subscription,
        known_user_id=user_id,
    )
    return "reconciled" if applied else "superseded"


async def try_reconcile_stale_paid_entitlement(
    *,
    pool: Pool,
    user_id: str,
    settings: Optional[StripeSettings] = None,
    now: Optional[datetime] = None,
) -> str:
    """Best-effort reconciliation that never makes login/account reads fail."""

    try:
        return await reconcile_stale_paid_entitlement(
            pool=pool,
            user_id=user_id,
            settings=settings,
            now=now,
        )
    except Exception:
        logger.exception(
            "Unable to reconcile stale Stripe entitlement for user %s",
            user_id,
        )
        return "failed"


def _first_subscription_item(subscription: dict) -> dict:
    """Return the single recurring item used by the four-plan catalog."""

    items = _mapping(subscription.get("items")).get("data")
    if isinstance(items, list) and items:
        return _mapping(items[0])
    return {}


def _object_id(value: Any) -> Optional[str]:
    """Extract a Stripe ID from either a string or expanded object."""

    if isinstance(value, str) and value:
        return value
    if isinstance(value, dict):
        return _string_or_none(value.get("id"))
    return None


def _mapping(value: Any) -> dict:
    """Normalize a possible Stripe mapping to a plain dictionary."""

    return value if isinstance(value, dict) else {}


def _string_or_none(value: Any) -> Optional[str]:
    """Return a non-empty string representation when one is present."""

    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _timestamp(value: Any) -> Optional[datetime]:
    """Convert Stripe epoch seconds or datetime values to aware UTC."""

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None
