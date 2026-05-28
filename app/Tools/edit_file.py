from typing import Optional
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import Tool as LangchainTool

from app.Tools.base_tool import BaseTool
from app.LLM.memory import Memory
from app.Types.agent_types import EditFileToolInput
from app.helper import send_last_assistant_message
from app.Agentic_Tools.file_editor import FileEditor


class EditFileTool(BaseTool):
    def __init__(self, llm: BaseChatModel, task_id: str, chat_id: str, memory: Optional[Memory] = None):
        super().__init__(
            name="edit_file",
            description="""
                Use this tool to make an edit to an existing file.\n\nThis will be read by a less intelligent model,
                which will quickly apply the edit. You should make it clear what the edit is, while also minimizing the unchanged code you write.
                \nWhen writing the edit, you should specify each edit in sequence, with the special comment // ... existing code ... to represent
                unchanged code in between edited lines.\n\nFor example:\n\n// ... existing code ...\nFIRST_EDIT\n// ... existing code ...\nSECOND_EDIT\n//
                ... existing code ...\nTHIRD_EDIT\n// ... existing code ...\n\nYou should still bias towards repeating as few lines of the original 
                file as possible to convey the change.\nBut, each edit should contain sufficient context of unchanged lines around the code you're 
                editing to resolve ambiguity.\nDO NOT omit spans of pre-existing code (or comments) without using the // ... existing code ... comment 
                to indicate its absence. If you omit the existing code comment, the model may inadvertently delete these lines.\nIf you plan on deleting 
                a section, you must provide context before and after to delete it. If the initial code is ```code \\n Block 1 \\n Block 2 \\n Block 
                3 \\n code```, and you want to remove Block 2, you would output ```// ... existing code ... \\n Block 1 \\n  Block 3 
                \\n // ... existing code ...```.\nMake sure it is clear what the edit should be, and where it should be applied.\nALWAYS make all 
                edits to a file in a single edit_file instead of multiple edit_file calls to the same file. The apply model can handle many distinct 
                edits at once.
            """,
            memory=memory,
            args_schema=EditFileToolInput,
        )
        self.chat_id = chat_id
        self.task_id = task_id
        self.file_editor = FileEditor(llm=llm, task_id=task_id, chat_id=chat_id, memory=memory)

    async def run(self, inputs: EditFileToolInput):
        # Send the assistant's message to client and get tool_call_id
        tool_call_id = await send_last_assistant_message(
            memory=self.memory, 
            task_id=self.task_id, 
            chat_id=self.chat_id,
            tool_name="edit_file"
        )

        response = await self.file_editor.edit_file(
            target_file=inputs.path,
            instructions=inputs.instructions,
            code_edit=inputs.code_edit,
            hide=inputs.hide,
            tool_call_id=tool_call_id,
        )

        return response