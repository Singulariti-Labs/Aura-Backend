from asyncpg import Pool
from datetime import datetime

import uuid

async def create_task(
    pool: Pool,
    task_id: str,
    chat_id: str,
    query: str,
    user_id: str,
):
    # Generate a random ID for the task
    id = str(uuid.uuid4())
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO tasks (id, task_id, user_id, chat_id, query, status, started_at, is_star, is_delete)
                VALUES ($1, $2, $3, $4, $5, 'running', $6, $7, $8)
            """,
            id,
            task_id,
            user_id,
            chat_id,
            query,
            datetime.utcnow(),
            False,
            False
        )
    except Exception as e:
        print("❌ INSERT FAILED")

# Get all tasks for a specific user
async def get_tasks_by_user(pool: Pool, user_id: str):
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM tasks WHERE user_id = $1 AND is_delete = FALSE ORDER BY started_at DESC",
                user_id
            )
            return [dict(row) for row in rows]
    except Exception as e:
        print("❌ FETCH TASKS FAILED")
        print("   error:", e)
        return []

# Get a specific task by task_id
async def get_task_by_task_id(pool: Pool, task_id: str):
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM tasks WHERE task_id = $1",
                task_id
            )
            return dict(row) if row else None
    except Exception as e:
        print("❌ FETCH TASK BY ID FAILED")
        print("   error:", e)
        return None

# Get a specific task by task_id
async def get_task_by_id(pool: Pool, id: str):
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM tasks WHERE id = $1",
                id
            )
            return dict(row) if row else None
    except Exception as e:
        print("❌ FETCH TASK BY ID FAILED")
        print("   error:", e)
        return None

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

# Delete a task (soft delete) set is_delete = True
async def delete_task_db(pool: Pool, id: str):
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE tasks SET is_delete = TRUE WHERE id = $1",
                id
            )
            return True
    except Exception as e:
        print(f"❌ DELETE TASK FAILED: {e}")
        return False

# Star a task
async def star_task_db(pool: Pool, id: str):
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE tasks SET is_star = TRUE WHERE id = $1",
                id
            )
            return True
    except Exception as e:
        print(f"❌ STAR TASK FAILED: {e}")
        return False

# Unstar a task
async def unstar_task_db(pool: Pool, id: str):
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE tasks SET is_star = FALSE WHERE id = $1",
                id
            )
            return True
    except Exception as e:
        print(f"❌ UNSTAR TASK FAILED: {e}")
        return False