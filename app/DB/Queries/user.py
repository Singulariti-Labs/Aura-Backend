from asyncpg import Pool
from datetime import datetime
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
