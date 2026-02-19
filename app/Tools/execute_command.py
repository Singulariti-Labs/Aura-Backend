from typing import Optional
from langchain_core.language_models.chat_models import BaseChatModel
import uuid

from app.Tools.base_tool import BaseTool
from app.LLM.memory import Memory
from app.Types.agent_types import ExecuteCommandToolInput
from app.helper import send_last_assistant_message
from app.Agentic_Tools.command_executor import CommandExecutor

class ExecuteCommandTool(BaseTool):
    def __init__(self, llm: BaseChatModel, task_id: str, chat_id: str,  memory: Optional[Memory] = None):
        """
        Initializes the Execute Command Tool with a language model and optional memory.

        Input:
        - llm: A chat-based LLM instance that powers the SupervisorAgent.
        - task_id: A unique identifier for a task.
        - chat_id: A unique identifier for a chat.
        - memory: Optional memory object to retain conversation history/context.
        """
        super().__init__(
            name="execute_command",
            description="""Execute a shell command on the target system in the current working directory currentWorkDir""",
            memory=memory,
            args_schema=ExecuteCommandToolInput
        )
        self.chat_id = chat_id
        self.task_id = task_id
        self.command_executor = CommandExecutor(llm=llm, task_id=task_id, chat_id=chat_id, memory=memory)

    async def run(self, inputs: ExecuteCommandToolInput):

        # Sending the last assistant message to the client.
        await send_last_assistant_message(memory=self.memory, task_id=self.task_id, chat_id=self.chat_id, tool_name="execute_command")
        
        tool_call_id = str(uuid.uuid4())
        response = await self.command_executor.execute_command(
            command=inputs.command,
            description=inputs.description,
            system=inputs.system,
            currentWorkDir=inputs.currentWorkDir,
            env=inputs.env,
            yieldMs=inputs.yieldMs,
            background=inputs.background,
            timeout=inputs.timeout,
            pty=inputs.pty,
            security=inputs.security,
            ask=inputs.ask,
            tool_call_id=tool_call_id
        )
        return response
