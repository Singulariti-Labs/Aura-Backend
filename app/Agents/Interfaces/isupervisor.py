from app.Types.agent_types import SystemInfo
from typing import Optional, Dict, List, Any, Literal
from langchain_core.language_models.chat_models import BaseChatModel

from app.Agents.base_agent import BaseAgent
from app.LLM.memory import Memory
from app.Types.agent_types import LLMConfig, Role


ROLE_TYPE = Literal["user", "assistant", "system", "tool"]

class ISupervisorAgent(BaseAgent):
    def __init__(self, llm: BaseChatModel, memory: Optional[Memory] = None, maxTokens: int = 128000):
        """
        Initialize the SupervisorAgent.

        Input:
            query (str): The main user query.
            llm (LLMConfig): Configuration for the language model.
            memory (Memory): Memory of the Agent
            maxTokens (int, optional): Maximum token count for responses. Defaults to 128000.
        """

    async def processQuery(self, query: str) -> List[Dict[str, Any]]:
        """
        Passes the user query to the PlannerAgent to generate a structured plan
        containing one or more steps to accomplish the task.

        Input:
            query (str): The user's input query.

        Returns:
            str: A serialized plan representing the task breakdown.
        """
    
    async def invoke(self, query: str, system_info: Optional[SystemInfo] = None, screenshot: Optional[str] = None) -> str:
        """
        Entry point to handle a user query. It stores the query in memory, calls
        the planner agent to generate a plan, and then executes the plan using either
        a simple or complex task execution flow.

        Input:
            query (str): The user query.
            system_info (Optional[SystemInfo]): Optional contextual system data.
            screenshot (Optional[str]): Optional base64 screenshot for additional context.

        Returns:
            str: The final response or result after processing the plan.
        """
    
    def update_memory(self, role: ROLE_TYPE, content: str, base64_image: Optional[str] = None, **kwargs) -> None:
        """
        Adds a message to the agent’s memory based on the sender role.

        Input:
            role (ROLE_TYPE): The message sender role (user, system, assistant, tool).
            content (str): Message text content.
            base64_image (Optional[str]): Base64 encoded screenshot, if any.
            **kwargs: Additional metadata such as tool_call_id.

        Raises:
            ValueError: If the provided role is unsupported.
        """

    async def handle_simple_task(self, plan: List[Dict[str, Any]]) -> str:
        """
        Executes a single-step plan as a simple task.

        Input:
            plan (List[Dict[str, Any]]): The plan containing a single task step.

        Returns:
            str: The result from executing the single step.

        Raises:
            ValueError: If the plan does not contain exactly one step.
        """

    async def handle_complex_task(self, plan: List[Dict[str, Any]]) -> str:
        """
        Executes a multi-step plan where steps may have dependencies. Tracks
        step completion status and retries failed steps up to a limit.

        Input:
            plan (List[Dict[str, Any]]): The structured plan containing multiple steps.

        Returns:
            str: The result of the final step or an error message if steps failed.
        """

    async def run_step(self, step: Dict[str, Any]) -> str:
        """
        Executes a single step by invoking the relevant sub-agent or tool.
        Prepares context using memory and LLM before executing the tool.

        Input:
            step (Dict[str, Any]): A dictionary describing the step to be executed.

        Returns:
            str: The result from the tool/sub-agent execution.

        Raises:
            Exception: If the tool execution or LLM call fails.
        """