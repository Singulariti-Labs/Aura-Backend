from abc import ABC, abstractmethod
from typing import Any
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables.base import Runnable

from app.Agents.base_agent import BaseAgent

class IPlannerAgent(BaseAgent):
    """
    Interface for PlannerAgent used by the SupervisorAgent to delegate planning tasks.
    """

    async def run(self, llm: BaseChatModel, query: str) -> Any:
         """
        Executes the planner logic by passing the query to the appropriate LLM pipeline.

        Input:
            llm (BaseChatModel): An instance of the language model (e.g., OpenAI or Anthropic).
            query (str): The user’s question or task description.

        Returns:
            Any: The output from the LLM—either a structured plan or a single-step task description.

        Raises:
            RuntimeError: If any error occurs during planning or while calling the LLM.
        """