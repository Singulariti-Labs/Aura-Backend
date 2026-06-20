import asyncio
import os
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import AsyncIterator, DefaultDict


class STTConnectionLimiter:
    """In-process limiter that protects this worker from unbounded STT streams."""

    def __init__(self) -> None:
        self.max_global_sessions = int(os.getenv("STT_MAX_ACTIVE_SESSIONS", "200"))
        self.max_sessions_per_user = int(os.getenv("STT_MAX_SESSIONS_PER_USER", "2"))
        self._active_global = 0
        self._active_by_user: DefaultDict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    async def acquire(self, user_id: str) -> bool:
        """Reserve one realtime STT slot for a user if capacity is available."""

        async with self._lock:
            if self._active_global >= self.max_global_sessions:
                return False
            if self._active_by_user[user_id] >= self.max_sessions_per_user:
                return False

            self._active_global += 1
            self._active_by_user[user_id] += 1
            return True

    async def release(self, user_id: str) -> None:
        """Release a previously reserved realtime STT slot."""

        async with self._lock:
            self._active_global = max(0, self._active_global - 1)
            if self._active_by_user[user_id] <= 1:
                self._active_by_user.pop(user_id, None)
            else:
                self._active_by_user[user_id] -= 1

    @asynccontextmanager
    async def session(self, user_id: str) -> AsyncIterator[bool]:
        """Context manager used by websocket routes to avoid leaked counters."""

        acquired = await self.acquire(user_id)
        try:
            yield acquired
        finally:
            if acquired:
                await self.release(user_id)


stt_connection_limiter = STTConnectionLimiter()
