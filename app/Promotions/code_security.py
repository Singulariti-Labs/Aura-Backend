"""Generation, normalization, masking, and hashing for promotional codes."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets


PROMO_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PAID_PLAN_PREFIXES = {"mini": "MINI", "pro": "PRO", "max": "MAX"}


class PromotionConfigurationError(RuntimeError):
    """Raised when secure promotion configuration is missing or unsafe."""


def normalize_promo_code(code: str) -> str:
    """Return one canonical uppercase code containing only letters and digits.

    Users may paste codes with spaces or hyphens. Removing separators before
    hashing makes those harmless formatting differences equivalent.
    """

    return re.sub(r"[^A-Z0-9]", "", str(code or "").upper())


def hash_promo_code(code: str, *, pepper: str) -> str:
    """Create the deterministic HMAC-SHA256 lookup hash for a promo code.

    A keyed hash prevents an attacker who obtains the database from testing a
    list of guessed promo codes without also knowing the server-side pepper.
    """

    normalized = normalize_promo_code(code)
    if len(normalized) < 16:
        raise ValueError("Promo code is too short")
    if len(pepper) < 32:
        raise PromotionConfigurationError(
            "PROMO_CODE_PEPPER must contain at least 32 characters"
        )
    return hmac.new(
        pepper.encode("utf-8"),
        normalized.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def generate_promo_code(plan_code: str) -> str:
    """Generate a high-entropy, human-readable code for a paid plan.

    Five groups of five base32-like characters provide roughly 125 bits of
    randomness while avoiding visually ambiguous characters such as 0/O/1/I.
    """

    prefix = PAID_PLAN_PREFIXES.get(plan_code)
    if prefix is None:
        raise ValueError("Promo codes can only grant mini, pro, or max")
    groups = [
        "".join(secrets.choice(PROMO_ALPHABET) for _ in range(5))
        for _ in range(5)
    ]
    return "-".join([prefix, *groups])


def mask_promo_code(code: str) -> str:
    """Return a safe database/dashboard hint that cannot redeem the code."""

    normalized = normalize_promo_code(code)
    prefix = next(
        (value for value in PAID_PLAN_PREFIXES.values() if normalized.startswith(value)),
        "PROMO",
    )
    return f"{prefix}-****-{normalized[-4:]}"


def get_promo_code_pepper() -> str:
    """Load and validate the secret used for promo-code lookup hashes."""

    value = (os.getenv("PROMO_CODE_PEPPER") or "").strip().strip('"').strip("'")
    if not value or value.upper().startswith("GENERATE-") or len(value) < 32:
        raise PromotionConfigurationError(
            "PROMO_CODE_PEPPER is not configured with at least 32 characters"
        )
    return value
