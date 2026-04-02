from typing import Optional
from langchain_core.language_models.chat_models import BaseChatModel
import uuid

from app.Tools.base_tool import BaseTool
from app.LLM.memory import Memory
from app.Types.agent_types import LSToolInput
from app.helper import send_last_assistant_message
from app.Agentic_Tools.coading_tools import CoadingTools

class LSTool(BaseTool):
    def __init__(self, llm: BaseChatModel, task_id: str, chat_id: str,  memory: Optional[Memory] = None):
        """
        Initializes the LS Tool with a language model and optional memory.

        Input:
        - llm: A chat-based LLM instance that powers the SupervisorAgent.
        - task_id: A unique identifier for a task.
        - chat_id: A unique identifier for a chat.
        - memory: Optional memory object to retain conversation history/context.
        """
        super().__init__(
            name="ls",
            description="""Lists files and directories in a given path. Use this to explore and understand the directory 
            layout before reading or modifying files. The path parameter must be absolute.You can optionally provide an 
            array of glob patterns to ignore with the ignore parameter. You should generally prefer the Glob and Grep tools, 
            if you know which directories to search.""",
            memory=memory,
            args_schema=LSToolInput
        )
        self.chat_id = chat_id
        self.task_id = task_id
        self.coading_tools = CoadingTools(llm=llm, task_id=task_id, chat_id=chat_id, memory=memory)

    async def run(self, inputs: LSToolInput):

        # Sending the last assistant message to the client.
        await send_last_assistant_message(memory=self.memory, task_id=self.task_id, chat_id=self.chat_id, tool_name="ls")
        
        tool_call_id = str(uuid.uuid4())
        response = await self.coading_tools.ls(
            path=inputs.path,
            ignore=inputs.ignore,
            currentWorkDir=inputs.currentWorkDir,
            hide=inputs.hide,
            tool_call_id=tool_call_id
        )
        return response
