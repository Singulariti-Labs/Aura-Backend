"""LangChain-facing wrapper for loading a named durable-memory file."""

from typing import Optional

from langchain_core.language_models.chat_models import BaseChatModel

from app.Agentic_Tools.memory_tools import MemoryTools
from app.LLM.memory import Memory
from app.Tools.base_tool import BaseTool
from app.Types.agent_types import ReadMemoryToolInput
from app.helper import send_last_assistant_message


READ_MEMORY_TOOL_DESCRIPTION = (
    """Read a specific named memory file when its description suggests it may 
    contain useful or relevant context for the current task. The system prompt 
    only contains memory file names, aliases, and descriptions, not the full 
    memory contents. Use this tool to load the full facts from a relevant 
    memory file before answering or acting. Do not call this tool for every 
    memory file; call it only when the memory description, or user request
    indicate the memory file is relevant."""
)


class ReadMemoryTool(BaseTool):
    """Provide the agent with the client-side ``read_memory`` action."""

    def __init__(
        self,
        llm: BaseChatModel,
        task_id: str,
        chat_id: str,
        memory: Optional[Memory] = None,
    ) -> None:
        """Initialize on-demand memory reads for the given task and chat."""

        super().__init__(
            name="read_memory",
            description=READ_MEMORY_TOOL_DESCRIPTION,
            memory=memory,
            args_schema=ReadMemoryToolInput,
        )
        self.task_id = task_id
        self.chat_id = chat_id
        self.memory_tools = MemoryTools(
            llm=llm,
            task_id=task_id,
            chat_id=chat_id,
            memory=memory,
        )

    async def run(self, inputs: ReadMemoryToolInput):
        """Forward the selected filename and target to the connected client."""

        tool_call_id = await send_last_assistant_message(
            memory=self.memory,
            task_id=self.task_id,
            chat_id=self.chat_id,
            tool_name="read_memory",
        )
        return await self.memory_tools.read_memory(
            name=inputs.name,
            target=inputs.target,
            tool_call_id=tool_call_id,
        )
