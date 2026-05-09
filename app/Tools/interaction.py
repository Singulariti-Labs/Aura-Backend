from typing import Optional
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import Tool as LangchainTool

from app.Tools.base_tool import BaseTool
from app.Tools.tool_input_parser import ToolInputParser
from app.Agents.interaction import InteractionAgent
from app.LLM.memory import Memory
from app.Types.agent_types import InteractionToolInput
from app.helper import send_last_assistant_message

class InteractionTool(BaseTool):
    def __init__(self, llm: BaseChatModel, task_id: str, chat_id: str,  memory: Optional[Memory] = None):
        """
        Initializes the SupervisorTool with a language model and optional memory.

        Input:
        - llm: A chat-based LLM instance that powers the SupervisorAgent.
        - task_id: A unique identifier for a task.
        - chat_id: A unique identifier for a chat.
        - memory: Optional memory object to retain conversation history/context.

        The constructor sets up a `SupervisorAgent` internally to handle task planning and delegation.
        """
        super().__init__(
            name="interaction_agent",
            description="""Interaction Agent simulates human actions like clicking, typing, and navigating to control applications and 
                    websites in real time. use interaction agent if there is any interaction required to perform on system UI""",
            memory=memory,
            args_schema=InteractionToolInput
        )
        self.chat_id = chat_id
        self.task_id = task_id
        self.interaction_agent = InteractionAgent(llm=llm, task_id=task_id, chat_id=chat_id, memory=memory)

    async def run(self, inputs: InteractionToolInput):

        # Sending the last assistant message to the client and get tool_call_id
        tool_call_id = await send_last_assistant_message(
            memory=self.memory, 
            task_id=self.task_id, 
            chat_id=self.chat_id, 
            tool_name="interaction"
        )
        query = inputs.query
        system_info = inputs.system_info
        response = await self.interaction_agent.invoke(
            query=query,
            system_info=system_info,
            tool_call_id = tool_call_id
        )
        return response