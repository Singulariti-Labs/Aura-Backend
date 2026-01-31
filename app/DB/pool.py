from dotenv import load_dotenv
import asyncpg
import os

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL not set")

_pool: asyncpg.Pool | None = None

async def init_db_pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=10,
            max_size=50,     # tune based on load
            timeout=60,
            command_timeout=60,
            statement_cache_size=0,  # important for Neon
        )
        print("✅ Neon PostgreSQL DB connected successfully!")
    return _pool


async def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized")
    return _pool


async def close_db_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        print("ℹ️ Neon PostgreSQL DB pool closed")