from typing import Optional
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import Tool as LangchainTool

from app.Tools.base_tool import BaseTool
from app.LLM.memory import Memory
from app.Types.agent_types import DeleteFileToolInput
from app.helper import send_last_assistant_message
from app.Agentic_Tools.file_editor import FileEditor

class DeleteFileTool(BaseTool):
    def __init__(self, llm: BaseChatModel, task_id: str, chat_id: str, memory: Optional[Memory] = None):
        super().__init__(
            name="delete_file",
            description="""Delete a file located at a given path. 
            The path must be relative to /singulariti_workspace (e.g., 'src/main.py' for /singulariti_workspace/src/main.py).""",
            memory=memory,
            args_schema=DeleteFileToolInput,
        )
        self.chat_id = chat_id
        self.task_id = task_id
        self.file_editor = FileEditor(llm=llm, task_id=task_id, chat_id=chat_id, memory=memory)

    async def run(self, inputs: DeleteFileToolInput) -> str:
        tool_call_id = await send_last_assistant_message(
            memory=self.memory, 
            task_id=self.task_id, 
            chat_id=self.chat_id, 
            tool_name="delete_file"
        )
        response = await self.file_editor.delete_file(
            path=inputs.path,
            hide=inputs.hide,
            tool_call_id=tool_call_id,
        )
        return response