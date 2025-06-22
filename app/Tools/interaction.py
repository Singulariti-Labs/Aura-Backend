from typing import Optional
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import Tool as LangchainTool
import uuid

from app.Tools.base_tool import BaseTool
from app.Tools.tool_input_parser import ToolInputParser
from app.Agents.interaction import InteractionAgent
from app.LLM.memory import Memory
from app.Types.agent_types import InteractionToolInput

class InteractionTool(BaseTool):
    def __init__(self, llm: BaseChatModel, task_id: str, memory: Optional[Memory] = None):
        """
        Initializes the SupervisorTool with a language model and optional memory.

        Input:
        - llm: A chat-based LLM instance that powers the SupervisorAgent.
        - task_id: A unique identifier for a task.
        - memory: Optional memory object to retain conversation history/context.

        The constructor sets up a `SupervisorAgent` internally to handle task planning and delegation.
        """
        super().__init__(
            name="supervisor",
            description="Plans and delegates tasks to sub-agents.",
            memory=memory,
            args_schema=InteractionToolInput
        )
        self.interaction_agent = InteractionAgent(llm=llm, task_id=task_id, memory=memory)

    async def run(self, inputs: InteractionToolInput) -> str:

        query = inputs.query
        system_info = inputs.system_info
        base64_image = inputs.base64_image
        tool_call_id = str(uuid.uuid4())
        response = await self.interaction_agent.invoke(
            query=query,
            system_info=system_info,
            screenshot=base64_image,
            tool_call_id = tool_call_id
        )
        return response