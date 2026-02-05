from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from app.api.auth_utils import get_current_user, get_user_info, auth_scheme
from app.DB.pool import get_pool
from app.DB.Queries.user import sync_user, get_user_by_auth0_id
from asyncpg import Pool
import logging

# Configure logger for auth routes
logger = logging.getLogger(__name__)

auth_router = APIRouter(prefix="/api/auth")

@auth_router.post("/sync")
async def sync_auth0_user(
    token: HTTPAuthorizationCredentials = Depends(auth_scheme),
    user_payload: dict = Depends(get_current_user)
):
    """
    Endpoint to sync Auth0 user info with local DB after successful login.
    """
    auth0_id = user_payload.get("sub")
    logger.info(f"🔄 Auth sync request received for user: {auth0_id}")
    
    pool = await get_pool()
    
    # Check if user already exists
    existing_user = await get_user_by_auth0_id(pool, auth0_id)
    
    # If user doesn't exist, try to fetch full profile from userinfo endpoint in aud
    if not existing_user:
        logger.info(f"👤 New user detected, fetching full profile for: {auth0_id}")
        aud = user_payload.get("aud")
        userinfo_url = None
        
        if isinstance(aud, list):
            for a in aud:
                if a.endswith("/userinfo"):
                    userinfo_url = a
                    break
        elif isinstance(aud, str) and aud.endswith("/userinfo"):
            userinfo_url = aud
            
        if userinfo_url:
            full_profile = await get_user_info(token.credentials, userinfo_url)
            logger.info(f"✅ Retrieved full user profile from Auth0 for: {auth0_id}")
            user_payload.update(full_profile)
    else:
        logger.info(f"✅ Existing user found in database: {existing_user.get('email', 'unknown')}")

    try:
        user = await sync_user(pool, user_payload)
        logger.info(f"✅ User synced successfully: {user.get('email', 'unknown')} (ID: {user.get('id')})")
        return {"status": "success", "user": user}
    except Exception as e:
        logger.error(f"❌ Failed to sync user {auth0_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@auth_router.get("/me")
async def get_me(user_payload: dict = Depends(get_current_user)):
    """
    Returns the current authenticated user's Auth0 payload.
    """
    logger.info(f"👤 User profile requested for: {user_payload.get('sub', 'unknown')}")
    return user_payload
