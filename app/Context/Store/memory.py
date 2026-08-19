import asyncio
from typing import Dict, Optional

from app.Context.models import ContextSnapshot
from app.Context.Store.base import ContextStore


class InMemoryContextStore(ContextStore):
    """Process-local store; replaceable by Redis through the same contract."""

    def __init__(self):
        self._contexts: Dict[str, ContextSnapshot] = {}
        self._lock = asyncio.Lock()

    async def get(self, context_id: str) -> Optional[ContextSnapshot]:
        async with self._lock:
            return self._contexts.get(context_id)

    async def save(self, context_id: str, context: ContextSnapshot) -> None:
        async with self._lock:
            # Keep the same object in memory so the live loop and store do not
            # duplicate a potentially large context. Redis will serialize it.
            self._contexts[context_id] = context

    async def delete(self, context_id: str) -> None:
        async with self._lock:
            self._contexts.pop(context_id, None)
