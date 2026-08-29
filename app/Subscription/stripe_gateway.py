"""Thin asynchronous wrapper around the official Stripe Python SDK."""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

import stripe

from app.Subscription.config import StripeSettings


@lru_cache(maxsize=2)
def _stripe_client(secret_key: str) -> stripe.StripeClient:
    """Create one reusable async HTTP client for each configured Stripe key."""

    return stripe.StripeClient(
        secret_key,
        max_network_retries=2,
        http_client=stripe.HTTPXClient(timeout=20),
    )


async def create_customer(
    *,
    settings: StripeSettings,
    user_id: str,
    email: str,
    name: str,
) -> str:
    """Create one Stripe Customer using a stable user-level idempotency key."""

    customer = await _stripe_client(settings.secret_key).v1.customers.create_async(
        {
            "email": email,
            "name": name,
            "metadata": {"user_id": user_id},
        },
        options={"idempotency_key": f"customer:{user_id}"},
    )
    return str(customer.id)


async def create_checkout_session(
    *,
    settings: StripeSettings,
    user_id: str,
    stripe_customer_id: str,
    plan_code: str,
    stripe_price_id: str,
    request_id: str,
) -> dict:
    """Create a hosted monthly subscription Checkout Session."""

    success_url = _with_checkout_session_id(settings.success_url)
    session = await _stripe_client(
        settings.secret_key
    ).v1.checkout.sessions.create_async(
        {
            "mode": "subscription",
            "customer": stripe_customer_id,
            "client_reference_id": user_id,
            "line_items": [{"price": stripe_price_id, "quantity": 1}],
            "success_url": success_url,
            "cancel_url": settings.cancel_url,
            "metadata": {
                "user_id": user_id,
                "plan_code": plan_code,
            },
            "subscription_data": {
                "metadata": {
                    "user_id": user_id,
                    "plan_code": plan_code,
                }
            },
        },
        options={
            "idempotency_key": f"checkout:{user_id}:{plan_code}:{request_id}"
        },
    )
    return {"session_id": str(session.id), "url": str(session.url)}


async def create_portal_session(
    *,
    settings: StripeSettings,
    stripe_customer_id: str,
) -> dict:
    """Create a short-lived Stripe Customer Portal session."""

    session = await _stripe_client(
        settings.secret_key
    ).v1.billing_portal.sessions.create_async(
        {
            "customer": stripe_customer_id,
            "return_url": settings.portal_return_url,
        }
    )
    return {"url": str(session.url)}


async def retrieve_subscription(
    *,
    settings: StripeSettings,
    stripe_subscription_id: str,
) -> dict:
    """Fetch Stripe's latest subscription state for missed-webhook recovery."""

    subscription = await _stripe_client(
        settings.secret_key
    ).v1.subscriptions.retrieve_async(stripe_subscription_id)
    return subscription.to_dict_recursive()


def verify_webhook(
    *,
    settings: StripeSettings,
    payload: bytes,
    signature: Optional[str],
) -> dict:
    """Verify a Stripe signature against the exact, unmodified request body."""

    if not signature:
        raise stripe.SignatureVerificationError(
            "Missing Stripe-Signature header",
            signature or "",
        )
    event = stripe.Webhook.construct_event(
        payload,
        signature,
        settings.webhook_secret,
    )
    return event.to_dict_recursive()


def _with_checkout_session_id(url: str) -> str:
    """Append Stripe's literal Checkout Session placeholder to a trusted URL."""

    if "{CHECKOUT_SESSION_ID}" in url:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}session_id={{CHECKOUT_SESSION_ID}}"
