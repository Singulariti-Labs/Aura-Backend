"""Small, versioned PostgreSQL migration runner for the application.

The project uses asyncpg directly instead of an ORM.  Keeping migrations next
to the database package preserves that structure while still making schema
changes repeatable across development, staging, and production environments.
"""

from __future__ import annotations

import logging
from pathlib import Path

from asyncpg import Pool


logger = logging.getLogger(__name__)
MIGRATIONS_DIRECTORY = Path(__file__).with_name("migrations")
MIGRATION_LOCK_NAME = "compute_agent_schema_migrations"


async def run_db_migrations(pool: Pool) -> None:
    """Apply each pending ``.sql`` migration exactly once.

    A PostgreSQL advisory lock serializes migration work when multiple server
    instances start at the same time.  Each file is committed together with
    its migration record, so a failed migration is safe to retry.
    """

    migration_files = sorted(MIGRATIONS_DIRECTORY.glob("*.sql"))
    if not migration_files:
        return

    async with pool.acquire() as connection:
        await connection.execute(
            "SELECT pg_advisory_lock(hashtext($1))",
            MIGRATION_LOCK_NAME,
        )
        try:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            applied_versions = {
                row["version"]
                for row in await connection.fetch(
                    "SELECT version FROM schema_migrations"
                )
            }

            for migration_file in migration_files:
                version = migration_file.stem
                if version in applied_versions:
                    continue

                sql = migration_file.read_text(encoding="utf-8")
                logger.info("Applying database migration %s", version)
                async with connection.transaction():
                    await connection.execute(sql)
                    await connection.execute(
                        "INSERT INTO schema_migrations (version) VALUES ($1)",
                        version,
                    )
                logger.info("Applied database migration %s", version)
        finally:
            await connection.execute(
                "SELECT pg_advisory_unlock(hashtext($1))",
                MIGRATION_LOCK_NAME,
            )
