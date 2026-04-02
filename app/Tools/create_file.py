from typing import Optional
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import Tool as LangchainTool
import uuid

from app.Tools.base_tool import BaseTool
from app.LLM.memory import Memory
from app.Types.agent_types import CreateFileToolInput
from app.helper import send_last_assistant_message
from app.Agentic_Tools.file_editor import FileEditor

class CreateFileTool(BaseTool):
    def __init__(self, llm: BaseChatModel, task_id: str, chat_id: str,  memory: Optional[Memory] = None):
        """
        Initializes the File Editor Tool with a language model and optional memory.

        Input:
        - llm: A chat-based LLM instance that powers the SupervisorAgent.
        - task_id: A unique identifier for a task.
        - chat_id: A unique identifier for a chat.
        - memory: Optional memory object to retain conversation history/context.
        """
        super().__init__(
            name="create_file",
            description="""Create a new file with the provided contents at a given path.
                (e.g., 'src/main.py' or doc/my_report.md)""",
            memory=memory,
            args_schema=CreateFileToolInput
        )
        self.chat_id = chat_id
        self.task_id = task_id
        self.file_editor = FileEditor(llm=llm, task_id=task_id, chat_id=chat_id, memory=memory)

    async def run(self, inputs: CreateFileToolInput):

        # Sending the last assistant message to the client.
        await send_last_assistant_message(memory=self.memory, task_id=self.task_id, chat_id=self.chat_id, tool_name="create_file")
        path = inputs.path
        content = inputs.content
        permissions = inputs.permissions
        hide = inputs.hide
        tool_call_id = str(uuid.uuid4())
        response = await self.file_editor.create_file(
            path=path,
            content=content,
            permissions=permissions,
            hide=hide,
            tool_call_id = tool_call_id
        )
        return response