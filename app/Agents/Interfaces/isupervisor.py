from app.Types.agent_types import SystemInfo
from typing import Optional, Dict, List, Any, Literal
from langchain_core.output_parsers import JsonOutputParser

from app.Agents.base_agent import BaseAgent
from app.Types.agent_types import LLMConfig, Role


ROLE_TYPE = Literal["user", "assistant", "system", "tool"]

class ISupervisorAgent(BaseAgent):
    def __init__(self, query: str, llm: LLMConfig, maxTokens: int = 128000):
        """
        Initialize the SupervisorAgent.

        Args:
            query (str): The main user query.
            llm (LLMConfig): Configuration for the language model.
            maxTokens (int, optional): Maximum token count for responses. Defaults to 128000.
        """

    async def invoke(self, query: str, systemInfo: SystemInfo, screenShot: Optional[str] = None) -> str:
        """
        Main entry point to invoke the SupervisorAgent.

        Args:
            query (str): The user query to process.
            systemInfo (SystemInfo): The current system state and capabilities.
            screenShot (Optional[str]): Base64 encoded screenshot of the screen (if needed).
        
        Returns:
            str: Final result after executing all tasks.
        """

    async def processQuery(self, query: str) -> List[Dict[str, Any]]:
        """
        Send the query to the PlannerAgent and return the task plan (steps).

        Args:
            query (str): Task query from the user.
        
        Returns:
            List[Dict[str, Any]]: Steps representing the plan to complete the task.
        """

    async def handle_simple_task(self, plan: List[Dict[str, Any]]) -> str:
        """
        Handle a single-step (simple) task.

        Args:
            plan (List[Dict[str, Any]]): Plan with a single step.
        
        Returns:
            str: Result of the task.
        """

    async def handle_complex_task(self, plan: List[Dict[str, Any]]) -> str:
        """
        Handle a multi-step (complex) task.

        Args:
            plan (List[Dict[str, Any]]): Plan with multiple steps.
        
        Returns:
            str: Final result after all steps.
        """

    async def run_step(self, step: Dict[str, Any]) -> str:
        """
        Run a single task step and invoke the appropriate tool/sub-agent.

        Args:
            step (Dict[str, Any]): A step dictionary containing id, description, dependencies, etc.
        
        Returns:
            str: Output result from the tool/sub-agent execution.
        """

    def update_memory(self, role: ROLE_TYPE, content: str, base64_image: Optional[str] = None, **kwargs) -> None:
        """
        Add messages to the agent's memory for context tracking.

        Args:
            role (str): Role of the message (user, assistant, system, tool).
            content (str): The message text.
            base64_image (Optional[str]): Optional screenshot for visual context.
            **kwargs: Additional attributes for tool-specific memory updates.
        """ 