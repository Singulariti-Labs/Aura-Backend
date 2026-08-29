from abc import ABC, abstractmethod
from typing import Optional

from app.Context.models import ContextSnapshot


class ContextStore(ABC):
    @abstractmethod
    async def get(self, context_id: str) -> Optional[ContextSnapshot]: ...

    @abstractmethod
    async def save(self, context_id: str, context: ContextSnapshot) -> None: ...

    @abstractmethod
    async def delete(self, context_id: str) -> None: ...
