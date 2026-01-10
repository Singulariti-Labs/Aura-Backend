from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from app.api.auth_utils import get_current_user, get_user_info, auth_scheme
from app.DB.pool import get_pool
from app.DB.Queries.user import sync_user, get_user_by_auth0_id
from asyncpg import Pool

auth_router = APIRouter(prefix="/api/auth")

@auth_router.post("/sync")
async def sync_auth0_user(
    token: HTTPAuthorizationCredentials = Depends(auth_scheme),
    user_payload: dict = Depends(get_current_user)
):
    """
    Endpoint to sync Auth0 user info with local DB after successful login.
    """
    pool = await get_pool()
    
    auth0_id = user_payload.get("sub")
    
    # Check if user already exists
    existing_user = await get_user_by_auth0_id(pool, auth0_id)
    
    # If user doesn't exist, try to fetch full profile from userinfo endpoint in aud
    if not existing_user:
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
            print("USER FULL PROFILE: ", full_profile)
            user_payload.update(full_profile)

    try:
        user = await sync_user(pool, user_payload)
        return {"status": "success", "user": user}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@auth_router.get("/me")
async def get_me(user_payload: dict = Depends(get_current_user)):
    """
    Returns the current authenticated user's Auth0 payload.
    """
    return user_payload
