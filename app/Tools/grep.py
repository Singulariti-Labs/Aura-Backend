from typing import Optional
from langchain_core.language_models.chat_models import BaseChatModel
import uuid

from app.Tools.base_tool import BaseTool
from app.LLM.memory import Memory
from app.Types.agent_types import GrepToolInput
from app.helper import send_last_assistant_message
from app.Agentic_Tools.coading_tools import CoadingTools

class GrepTool(BaseTool):
    def __init__(self, llm: BaseChatModel, task_id: str, chat_id: str,  memory: Optional[Memory] = None):
        """
        Initializes the Grep Tool with a language model and optional memory.

        Input:
        - llm: A chat-based LLM instance that powers the SupervisorAgent.
        - task_id: A unique identifier for a task.
        - chat_id: A unique identifier for a chat.
        - memory: Optional memory object to retain conversation history/context.
        """
        super().__init__(
            name="grep",
            description="""Fast content search tool that works with any codebase size
- Searches file contents using regular expressions
- Supports full regex syntax (eg. "log.*Error", "function\\s+\\w+", etc.)
- Filter files by pattern with the include parameter (eg. "*.js", "*.{ts,tsx}")
- Returns file paths and line numbers with at least one match sorted by modification time
- Use this tool when you need to find files containing specific patterns
- If you need to identify/count the number of matches within files, use the Bash tool with `rg` (ripgrep) directly. Do NOT use `grep`.
- When you are doing an open-ended search that may require multiple rounds of globbing and grepping, use the Task tool instead""",
            memory=memory,
            args_schema=GrepToolInput
        )
        self.chat_id = chat_id
        self.task_id = task_id
        self.coading_tools = CoadingTools(llm=llm, task_id=task_id, chat_id=chat_id, memory=memory)

    async def run(self, inputs: GrepToolInput):

        # Sending the last assistant message to the client.
        await send_last_assistant_message(memory=self.memory, task_id=self.task_id, chat_id=self.chat_id, tool_name="grep")
        
        tool_call_id = str(uuid.uuid4())
        response = await self.coading_tools.grep(
            pattern=inputs.pattern,
            path=inputs.path,
            include=inputs.include,
            tool_call_id=tool_call_id
        )
        return response
