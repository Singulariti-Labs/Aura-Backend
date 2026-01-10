from asyncpg import Pool
from datetime import datetime
import uuid
from typing import Optional
from app.DB.models import User

async def get_user(pool: Pool, user_id: str) -> Optional[dict]:
    """
    Fetch user details by user_id.
    """
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, email, name, created_at, updated_at FROM users WHERE id = $1",
                user_id
            )
            if row:
                return dict(row)
            return None
    except Exception as e:
        print(f"❌ FETCH USER FAILED: {e}")
        return None

async def get_user_by_auth0_id(pool: Pool, auth0_id: str) -> Optional[dict]:
    """
    Fetch user by Auth0 ID.
    """
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM users WHERE auth0_id = $1",
                auth0_id
            )
            return dict(row) if row else None
    except Exception as e:
        print(f"❌ FETCH USER BY AUTH0 ID FAILED: {e}")
        return None

async def sync_user(pool: Pool, user_data: dict) -> dict:
    """
    Sync user profile from Auth0 to local DB.
    """
    auth0_id = user_data.get("sub")
    email = user_data.get("email") or f"{auth0_id}@placeholder.com" # Fallback to prevent DB error
    name = user_data.get("name") or email.split("@")[0]
    
    # Check if user already exists
    existing_user = await get_user_by_auth0_id(pool, auth0_id)
    
    try:
        async with pool.acquire() as conn:
            if existing_user:
                return existing_user
            else:
                # Create new user
                user_id = user_data.get("user_id") or str(uuid.uuid4())
                await conn.execute(
                    "INSERT INTO users (id, auth0_id, email, name, created_at) VALUES ($1, $2, $3, $4, $5)",
                    user_id, auth0_id, email, name, datetime.utcnow()
                )
                return await get_user_by_auth0_id(pool, auth0_id)
    except Exception as e:
        print(f"❌ SYNC USER FAILED: {e}")
        raise e
