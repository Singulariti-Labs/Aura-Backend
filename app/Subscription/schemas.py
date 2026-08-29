"""Validated request bodies for subscription endpoints."""

from typing import Literal

from pydantic import BaseModel, Field


PaidPlanCode = Literal["mini", "pro", "max"]


class CreateCheckoutSessionRequest(BaseModel):
    """Request to start a paid Stripe Checkout Session.

    ``request_id`` must be a fresh UUID-like value for a new user action.  A
    retry of the same client action must reuse it, allowing Stripe's
    idempotency support to prevent duplicate Checkout Sessions.
    """

    plan_code: PaidPlanCode
    request_id: str = Field(min_length=8, max_length=128)
