import os

from app.Context.Store.base import ContextStore
from app.Context.Store.memory import InMemoryContextStore


def create_context_store(backend: str | None = None) -> ContextStore:
    selected = (backend or os.getenv("CONTEXT_STORE_BACKEND", "memory")).lower()
    if selected == "memory":
        return InMemoryContextStore()
    if selected == "redis":
        raise RuntimeError(
            "CONTEXT_STORE_BACKEND=redis requires the future RedisContextStore adapter."
        )
    raise ValueError(f"Unsupported context store backend: {selected!r}")


context_store = create_context_store()
