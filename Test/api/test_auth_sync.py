import unittest
from unittest.mock import AsyncMock, MagicMock

from app.DB.Queries.user import sync_user


class _AsyncContext:
    def __init__(self, value=None):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class AuthSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_upsert_matches_partial_auth0_unique_index(self):
        connection = MagicMock()
        connection.execute = AsyncMock()
        connection.fetchrow = AsyncMock(
            return_value={
                "id": "user-1",
                "auth0_id": "auth0|123",
                "email": "user@example.com",
            }
        )
        connection.transaction.return_value = _AsyncContext()

        pool = MagicMock()
        pool.acquire.return_value = _AsyncContext(connection)

        user = await sync_user(
            pool,
            {
                "sub": "auth0|123",
                "email": "user@example.com",
                "name": "Test User",
            },
        )

        upsert_sql = " ".join(connection.fetchrow.await_args.args[0].split())
        self.assertIn(
            "ON CONFLICT (auth0_id) "
            "WHERE auth0_id IS NOT NULL "
            "DO UPDATE",
            upsert_sql,
        )
        self.assertEqual(user["id"], "user-1")
        self.assertEqual(connection.execute.await_count, 3)


if __name__ == "__main__":
    unittest.main()
