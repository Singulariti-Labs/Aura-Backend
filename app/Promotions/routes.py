"""Authenticated FastAPI routes for promotional plan access."""

from fastapi import APIRouter, Depends, HTTPException

from app.DB.Queries.user import get_user_by_auth0_id
from app.DB.pool import get_pool
from app.Promotions.code_security import PromotionConfigurationError
from app.Promotions.schemas import (
    RedeemPromotionRequest,
    RedeemPromotionResponse,
)
from app.Promotions.service import (
    InvalidPromotionError,
    PromotionConflictError,
    PromotionUserNotFoundError,
    redeem_promotion,
)
from app.api.auth_utils import get_current_user


promotion_router = APIRouter(prefix="/api/promotions", tags=["promotions"])


@promotion_router.post("/redeem", response_model=RedeemPromotionResponse)
async def redeem_promotion_code(
    body: RedeemPromotionRequest,
    current_user: dict = Depends(get_current_user),
):
    """Validate a promo and atomically grant its paid-tier entitlement."""

    pool = await get_pool()
    user = await get_user_by_auth0_id(pool, current_user.get("sub"))
    if not user:
        raise HTTPException(status_code=404, detail="User record not found")

    try:
        return await redeem_promotion(
            pool=pool,
            user_id=str(user["id"]),
            code=body.code,
        )
    except InvalidPromotionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PromotionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PromotionUserNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PromotionConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
