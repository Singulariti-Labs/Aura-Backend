"""Environment-backed Stripe subscription configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass


class SubscriptionConfigurationError(RuntimeError):
    """Raised when a billing endpoint is used before Stripe is configured."""


def get_billing_mode() -> str:
    """Return the active acquisition mode, defaulting safely to promotions.

    Set ``BILLING_MODE=stripe`` only after a payment account and all Stripe
    secrets are ready. Promo redemption remains available in either mode.
    """

    value = (os.getenv("BILLING_MODE") or "promo").strip().lower()
    return value if value in {"promo", "stripe"} else "promo"


def require_stripe_billing_mode() -> None:
    """Reject payment operations while the application is promo-only."""

    if get_billing_mode() != "stripe":
        raise SubscriptionConfigurationError(
            "Payment checkout is disabled while BILLING_MODE=promo"
        )


@dataclass(frozen=True, slots=True)
class StripeSettings:
    """Secrets and trusted redirect URLs used by server-side Stripe calls."""

    secret_key: str
    webhook_secret: str
    success_url: str
    cancel_url: str
    portal_return_url: str

    @classmethod
    def from_env(cls) -> "StripeSettings":
        """Load settings without exposing secret values in exceptions or logs."""

        return cls(
            secret_key=_required_env("STRIPE_SECRET_KEY"),
            webhook_secret=_required_env("STRIPE_WEBHOOK_SECRET"),
            success_url=_required_env("BILLING_SUCCESS_URL"),
            cancel_url=_required_env("BILLING_CANCEL_URL"),
            portal_return_url=_required_env("BILLING_PORTAL_RETURN_URL"),
        )


def _required_env(name: str) -> str:
    """Return a configured value and reject empty/example placeholders."""

    value = (os.getenv(name) or "").strip().strip('"').strip("'")
    invalid_prefixes = ("YOUR ", "SK_TEST_...", "WHSEC_...", "HTTPS://APP.EXAMPLE.COM")
    if not value or value.upper().startswith(invalid_prefixes):
        raise SubscriptionConfigurationError(f"{name} is not configured")
    return value
