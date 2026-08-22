"""FastAPI routes for subscription catalog, Checkout, Portal, and webhooks."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.DB.Queries.subscription import (
    get_user_billing_summary,
    list_active_plans,
)
from app.DB.Queries.user import get_user_by_auth0_id
from app.DB.Queries.promotion import refresh_expired_user_promotion
from app.DB.pool import get_pool
from app.Subscription.config import (
    StripeSettings,
    SubscriptionConfigurationError,
    get_billing_mode,
    require_stripe_billing_mode,
)
from app.Subscription.schemas import CreateCheckoutSessionRequest
from app.Subscription.service import (
    SubscriptionConflictError,
    SubscriptionNotReadyError,
    process_verified_webhook,
    start_checkout,
    start_customer_portal,
    try_reconcile_stale_paid_entitlement,
)
from app.Subscription.stripe_gateway import verify_webhook
from app.api.auth_utils import get_current_user


logger = logging.getLogger(__name__)
subscription_router = APIRouter(prefix="/api/subscriptions", tags=["subscriptions"])


@subscription_router.get("/plans")
async def fetch_subscription_plans():
    """Return the public, locally cached four-plan catalog."""

    pool = await get_pool()
    return {
        "billing_mode": get_billing_mode(),
        "plans": await list_active_plans(pool),
    }


@subscription_router.get("/me")
async def fetch_my_subscription(
    current_user: dict = Depends(get_current_user),
):
    """Return the user's entitlement and current 12-hour usage snapshot.

    The public response uses ``access_expires_at`` while the internal database
    column remains ``access_until``. This keeps client naming consistent with
    the promotion redemption endpoint without changing entitlement storage.
    """

    pool = await get_pool()
    user = await get_user_by_auth0_id(pool, current_user.get("sub"))
    if not user:
        raise HTTPException(status_code=404, detail="User record not found")

    user_id = str(user["id"])
    await refresh_expired_user_promotion(
        pool,
        user_id=user_id,
        now=datetime.now(timezone.utc),
    )
    if get_billing_mode() == "stripe":
        await try_reconcile_stale_paid_entitlement(
            pool=pool,
            user_id=user_id,
        )
    summary = await get_user_billing_summary(pool, user_id)
    if not summary:
        raise HTTPException(status_code=404, detail="Billing record not found")
    return {**summary, "billing_mode": get_billing_mode()}


@subscription_router.post("/checkout-session")
async def create_subscription_checkout(
    body: CreateCheckoutSessionRequest,
    current_user: dict = Depends(get_current_user),
):
    """Create a Stripe-hosted Checkout URL for Mini, Pro, or Max."""

    pool = await get_pool()
    user = await get_user_by_auth0_id(pool, current_user.get("sub"))
    if not user:
        raise HTTPException(status_code=404, detail="User record not found")

    try:
        require_stripe_billing_mode()
        return await start_checkout(
            pool=pool,
            settings=StripeSettings.from_env(),
            user=user,
            plan_code=body.plan_code,
            request_id=body.request_id,
        )
    except SubscriptionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (SubscriptionConfigurationError, SubscriptionNotReadyError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except stripe.StripeError as exc:
        logger.exception("Stripe Checkout Session creation failed")
        raise HTTPException(
            status_code=502,
            detail="Stripe could not create the Checkout Session",
        ) from exc


@subscription_router.post("/portal-session")
async def create_subscription_portal(
    current_user: dict = Depends(get_current_user),
):
    """Create a Stripe-hosted customer billing portal URL."""

    pool = await get_pool()
    user = await get_user_by_auth0_id(pool, current_user.get("sub"))
    if not user:
        raise HTTPException(status_code=404, detail="User record not found")

    try:
        require_stripe_billing_mode()
        return await start_customer_portal(
            pool=pool,
            settings=StripeSettings.from_env(),
            user_id=str(user["id"]),
        )
    except (SubscriptionConfigurationError, SubscriptionNotReadyError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except stripe.StripeError as exc:
        logger.exception("Stripe Customer Portal Session creation failed")
        raise HTTPException(
            status_code=502,
            detail="Stripe could not create the Customer Portal Session",
        ) from exc


@subscription_router.post("/webhook", status_code=status.HTTP_200_OK)
async def receive_stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
):
    """Verify, durably record, and idempotently process a Stripe event."""

    payload = await request.body()
    try:
        require_stripe_billing_mode()
        settings = StripeSettings.from_env()
        event = verify_webhook(
            settings=settings,
            payload=payload,
            signature=stripe_signature,
        )
    except SubscriptionConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ValueError, stripe.SignatureVerificationError) as exc:
        raise HTTPException(status_code=400, detail="Invalid Stripe webhook") from exc

    try:
        processing_result = await process_verified_webhook(await get_pool(), event)
    except Exception as exc:
        # A non-2xx response asks Stripe to retry after transient failures.
        raise HTTPException(
            status_code=500,
            detail="Stripe webhook processing failed",
        ) from exc
    return {"status": processing_result}
