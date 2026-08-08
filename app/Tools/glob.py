from typing import Optional, List
from langchain_core.language_models.chat_models import BaseChatModel

from app.Tools.base_tool import BaseTool
from app.LLM.memory import Memory
from app.Types.agent_types import GlobToolInput
from app.helper import send_last_assistant_message
from app.Agentic_Tools.coading_tools import CoadingTools

class GlobTool(BaseTool):
    def __init__(self, llm: BaseChatModel, task_id: str, chat_id: str,  memory: Optional[Memory] = None):
        """
        Initializes the Glob Tool with a language model and optional memory.

        Input:
        - llm: A chat-based LLM instance that powers the SupervisorAgent.
        - task_id: A unique identifier for a task.
        - chat_id: A unique identifier for a chat.
        - memory: Optional memory object to retain conversation history/context.
        """
        super().__init__(
            name="glob",
            description="""Finds files and directories matching a given path pattern (e.g., "**/*.tsx", "src/**/*.css")
            within the specified current working directory. Returns matching file paths sorted by modification time. 
            Prefer this over ls when you know the file type or naming pattern you're looking for. Does not read file contents.""",
            memory=memory,
            args_schema=GlobToolInput
        )
        self.chat_id = chat_id
        self.task_id = task_id
        self.coading_tools = CoadingTools(llm=llm, task_id=task_id, chat_id=chat_id, memory=memory)

    async def run(self, inputs: GlobToolInput):

        # Sending the last assistant message to the client and get tool_call_id
        tool_call_id = await send_last_assistant_message(
            memory=self.memory, 
            task_id=self.task_id, 
            chat_id=self.chat_id, 
            tool_name="glob"
        )
        
        response = await self.coading_tools.glob(
            pattern=inputs.pattern,
            path=inputs.path,
            currentWorkDir=inputs.currentWorkDir,
            hide=inputs.hide,
            tool_call_id=tool_call_id
        )
        return response
