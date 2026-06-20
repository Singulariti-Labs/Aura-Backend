from typing import Optional
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import Tool as LangchainTool

from app.Tools.base_tool import BaseTool
from app.LLM.memory import Memory
from app.Types.agent_types import RewriteFileToolInput
from app.helper import send_last_assistant_message
from app.Agentic_Tools.file_editor import FileEditor

class RewriteFileTool(BaseTool):
    def __init__(self, llm: BaseChatModel, task_id: str, chat_id: str, memory: Optional[Memory] = None):
        super().__init__(
            name="rewrite_file",
            description="""Rewrite the entire file with the given new content. 
                The file path must be relative to /singulariti_workspace (e.g., 'src/main.py' for /singulariti_workspace/src/main.py). 
                IMPORTANT: Always prefer using edit_file for making changes to code. Only use this tool when edit_file fails or 
                when you need to replace the entire file content.
            """,
            memory=memory,
            args_schema=RewriteFileToolInput,
        )
        self.chat_id = chat_id
        self.task_id = task_id
        self.file_editor = FileEditor(llm=llm, task_id=task_id, chat_id=chat_id, memory=memory)

    async def run(self, inputs: RewriteFileToolInput):
        tool_call_id = await send_last_assistant_message(
            memory=self.memory, 
            task_id=self.task_id, 
            chat_id=self.chat_id, 
            tool_name="rewrite_file"
        )
        response = await self.file_editor.rewrite_file(
            path=inputs.path,
            content=inputs.content,
            permissions=inputs.permissions,
            hide=inputs.hide,
            tool_call_id=tool_call_id,
        )
        return response