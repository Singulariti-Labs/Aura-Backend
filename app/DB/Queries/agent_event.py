from asyncpg import Pool
from datetime import datetime
import json
import traceback
import uuid


async def create_agent_event(
    pool: Pool,
    task_id: str,
    role: str,
    message_type: str | None,
    tool: str | None,
    payload: dict,
    seq: int = 1,
):
    print("➡️ create_agent_event called")
    print("   task_id:", task_id)
    print("   seq:", seq)
    print("   role:", role)

    # Generate a random ID for the agent event
    id = str(uuid.uuid4())
    try:
        async with pool.acquire() as conn:
            print("✅ DB connection acquired")
            await conn.execute(
                """
                INSERT INTO agent_events
                (id, task_id, seq, role, message_type, tool, payload, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                id,
                task_id,
                seq,
                role,
                message_type,
                tool,
                json.dumps(payload),
                datetime.utcnow(),
            )

            print("🎉 INSERT SUCCESS")
    except Exception as e:
        print("❌ INSERT FAILED")
        print("   error:", e)
        print("   traceback:")
        traceback.print_exc()
        return None
