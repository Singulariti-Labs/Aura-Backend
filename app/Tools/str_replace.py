from typing import Optional
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import Tool as LangchainTool

from app.Tools.base_tool import BaseTool
from app.LLM.memory import Memory
from app.Types.agent_types import StrReplaceToolInput
from app.helper import send_last_assistant_message
from app.Agentic_Tools.file_editor import FileEditor

class StrReplaceTool(BaseTool):
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
            name="str_replace",
            description="""Replace specific text in a file. The file path must be relative to /singulariti_workspace (e.g., 'src/main.py' for 
                /singulariti_workspace/src/main.py). IMPORTANT: Prefer using edit_file for faster, shorter edits to avoid repetition. Only use this 
                tool when you need to replace a unique string that appears exactly once in the file and edit_file is not suitable.
            """,
            memory=memory,
            args_schema=StrReplaceToolInput
        )
        self.chat_id = chat_id
        self.task_id = task_id
        self.file_editor = FileEditor(llm=llm, task_id=task_id, chat_id=chat_id, memory=memory)

    async def run(self, inputs: StrReplaceToolInput):

        # Sending the last assistant message to the client and get tool_call_id
        tool_call_id = await send_last_assistant_message(
            memory=self.memory, 
            task_id=self.task_id, 
            chat_id=self.chat_id, 
            tool_name="str_replace"
        )
        path = inputs.path
        new_str = inputs.new_str
        old_str = inputs.old_str

        response = await self.file_editor.str_replace(
            path=path,
            new_str=new_str,
            old_str=old_str,
            hide=inputs.hide,
            tool_call_id = tool_call_id
        )
        return response