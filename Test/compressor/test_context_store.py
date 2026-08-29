import unittest

from app.Context.Store.memory import InMemoryContextStore


class ContextStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_delete_is_idempotent(self):
        store = InMemoryContextStore()
        await store.delete("missing")
        self.assertIsNone(await store.get("missing"))


if __name__ == "__main__":
    unittest.main()
