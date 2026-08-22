"""Validated API contracts for promotion redemption."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


PaidPlanCode = Literal["mini", "pro", "max"]


class RedeemPromotionRequest(BaseModel):
    """A user-supplied promo code submitted for authenticated redemption."""

    code: str = Field(min_length=8, max_length=128)

    @field_validator("code")
    @classmethod
    def code_must_contain_visible_text(cls, value: str) -> str:
        """Trim accidental whitespace and reject an effectively empty value."""

        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Promo code is required")
        return cleaned


class RedeemPromotionResponse(BaseModel):
    """The effective entitlement returned after a successful redemption."""

    plan_code: PaidPlanCode
    previous_plan_code: Literal["free", "mini", "pro", "max"]
    entitlement_source: Literal["promo"] = "promo"
    access_started_at: datetime
    access_expires_at: Optional[datetime]
    usage_limit_usd: str
    window_spent_usd: str
    message: str
