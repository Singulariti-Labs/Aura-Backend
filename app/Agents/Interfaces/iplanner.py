from abc import ABC, abstractmethod
from typing import Any
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables.base import Runnable

from app.Agents.base_agent import BaseAgent

class IPlannerAgent(BaseAgent):
    """
    Interface for PlannerAgent used by the SupervisorAgent to delegate planning tasks.
    """

    def create_agent(self, llm: BaseChatModel) -> Runnable:
        """
        Create an agent capable of processing structured task planning.

        Args:
            llm (BaseChatModel): The language model instance.

        Returns:
            Runnable: The configured agent.
        """

    async def execute(self, llm: BaseChatModel, agent: Runnable, query: str) -> Any:
        """
        Execute a given task plan using the agent.

        Args:
            llm (BaseChatModel): The language model.
            agent (Runnable): The agent object.
            query (str): The user's task query.

        Returns:
            Any: The result of execution.
        """

    async def run(self, llm: BaseChatModel, query: str) -> Any:
        """
        Main method to generate a structured plan or response from the planner.

        Args:
            llm (BaseChatModel): The language model instance.
            query (str): The task query from the user.

        Returns:
            Any: Structured task steps or simplified response.
        """