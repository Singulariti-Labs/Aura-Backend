from abc import ABC, abstractmethod
from typing import Optional
from app.Types.agent_types import LLMConfig, SystemInfo


class IAgent(ABC):
    def __init__(
        self,
        query: str,
        system_info: SystemInfo,
        llm: LLMConfig,
        maxTokens: int = 128000,
        screenshot: Optional[str] = None,
    ):
        """Initialize the agent with query, system info, LLM configuration, and optional screenshot."""
        pass

    
    async def invoke(self):
        """
        Execute the agent logic using the configured LLM and tools.

        Returns:
            Any: Result from the LLM agent.
        """
        pass