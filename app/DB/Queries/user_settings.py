from asyncpg import Pool
from datetime import datetime
import json
import uuid
from typing import Optional

async def upsert_user_settings(pool: Pool, user_id: str, settings_dict: dict):
    """
    Update or insert user settings.
    """
    settings_json = json.dumps(settings_dict)
    now = datetime.utcnow()
    
    try:
        async with pool.acquire() as conn:
            # Check if settings already exist for this user
            row = await conn.fetchrow(
                "SELECT id FROM user_settings WHERE user_id = $1",
                user_id
            )
            
            if row:
                # Update existing
                await conn.execute(
                    "UPDATE user_settings SET user_settings = $1, updated_at = $2 WHERE user_id = $3",
                    settings_json, now, user_id
                )
            else:
                # Insert new
                new_id = str(uuid.uuid4())
                await conn.execute(
                    "INSERT INTO user_settings (id, user_id, user_settings, created_at, updated_at) VALUES ($1, $2, $3, $4, $5)",
                    new_id, user_id, settings_json, now, now
                )
            return True
    except Exception as e:
        print(f"❌ UPSERT USER SETTINGS FAILED: {e}")
        return False

async def get_user_settings(pool: Pool, user_id: str) -> Optional[dict]:
    """
    Fetch user settings by user_id.
    """
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT user_settings FROM user_settings WHERE user_id = $1",
                user_id
            )
            if row and row['user_settings']:
                return json.loads(row['user_settings'])
            return None
    except Exception as e:
        print(f"❌ FETCH USER SETTINGS FAILED: {e}")
        return None
