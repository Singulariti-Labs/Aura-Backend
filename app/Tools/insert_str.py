from typing import Optional
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import Tool as LangchainTool
import uuid

from app.Tools.base_tool import BaseTool
from app.LLM.memory import Memory
from app.Types.agent_types import InsertStrToolInput
from app.helper import send_last_assistant_message
from app.Agentic_Tools.file_editor import FileEditor

class InsertStrTool(BaseTool):
    def __init__(self, llm: BaseChatModel, task_id: str, chat_id: str, memory: Optional[Memory] = None):
        super().__init__(
            name="insert_str",
            description="""Insert a new string at a given line number in a file. 
                The file path must be relative to /singulariti_workspace 
                (e.g., 'src/main.py' for /singulariti_workspace/src/main.py). 
                IMPORTANT: ALways prefer using edit_file for making any changes, use insert_str only if you need to add/insert only one single line 
                and you also know on which line too add it
            """,
            memory=memory,
            args_schema=InsertStrToolInput,
        )
        self.chat_id = chat_id
        self.task_id = task_id
        self.file_editor = FileEditor(llm=llm, task_id=task_id, chat_id=chat_id, memory=memory)

    async def run(self, inputs: InsertStrToolInput):
        # Send the assistant's message to client
        await send_last_assistant_message(
            memory=self.memory, 
            task_id=self.task_id, 
            chat_id=self.chat_id, 
            tool_name="insert_str"
        )

        tool_call_id = str(uuid.uuid4())

        response = await self.file_editor.insert_str(
            path=inputs.path,
            insert_line_no=inputs.insert_line_no,
            new_str=inputs.new_str,
            hide=inputs.hide,
            tool_call_id=tool_call_id,
        )

        return response