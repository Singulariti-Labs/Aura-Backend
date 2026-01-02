from asyncpg import Pool
from datetime import datetime

import uuid

# For now, using dummy user_id
DUMMY_USER_ID = "00000000-0000-0000-0000-000000000000"


async def create_task(
    pool: Pool,
    task_id: str,
    chat_id: str,
    query: str,
    user_id: str = DUMMY_USER_ID,
):
    # Generate a random ID for the task
    id = str(uuid.uuid4())
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO tasks (id, task_id, user_id, chat_id, query, status, started_at)
                VALUES ($1, $2, $3, $4, $5, 'running', $6)
            """,
            id,
            task_id,
            user_id,
            chat_id,
            query,
            datetime.utcnow()
        )
    except Exception as e:
        print("❌ INSERT FAILED")
        print("   error:", e)

# Update task status
async def update_task_status(pool: Pool, task_id: str, status: str):
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE tasks
                SET status=$1, finished_at=$2
                WHERE task_id=$3
            """,
            status, datetime.utcnow(), task_id
        )
    except Exception as e:
        print("❌ UPDATE FAILED")
        print("   error:", e)