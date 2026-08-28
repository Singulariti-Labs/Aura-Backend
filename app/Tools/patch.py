from typing import Optional

from langchain_core.language_models.chat_models import BaseChatModel

from app.Agentic_Tools.file_editor import FileEditor
from app.LLM.memory import Memory
from app.Tools.base_tool import BaseTool
from app.Types.agent_types import PatchToolInput
from app.helper import send_last_assistant_message


PATCH_TOOL_DESCRIPTION = """Targeted find-and-replace edits in files. Use this instead of sed/awk in terminal. Uses fuzzy matching (9 strategies) so minor whitespace/indentation differences won't break it. Returns a unified diff and automatically runs syntax checks after editing.

REPLACE MODE (mode='replace', default): find a string and replace it. Requires path, old_string, and new_string. old_string must be unique unless replace_all=true; multiple matches without replace_all are invalid and rejected. Include surrounding context lines when needed to ensure uniqueness. new_string must differ from old_string and may be an empty string to delete the matched text.

PATCH MODE (mode='patch'): apply V4A patches for bulk changes. Requires patch content only. A single patch may contain multiple *** Update File: sections for multi-file or multi-location edits. Format:
*** Begin Patch
*** Update File: path/to/file
@@ context hint @@
 context line
-removed line
+added line
*** End Patch"""


class PatchTool(BaseTool):
    """Expose client-side targeted and multi-file patch editing to the LLM."""

    def __init__(
        self,
        llm: BaseChatModel,
        task_id: str,
        chat_id: str,
        memory: Optional[Memory] = None,
    ):
        """Initialize the patch tool for one task and chat session."""

        super().__init__(
            name="patch",
            description=PATCH_TOOL_DESCRIPTION,
            memory=memory,
            args_schema=PatchToolInput,
        )
        self.chat_id = chat_id
        self.task_id = task_id
        self.file_editor = FileEditor(
            llm=llm,
            task_id=task_id,
            chat_id=chat_id,
            memory=memory,
        )

    async def run(self, inputs: PatchToolInput):
        """Forward validated patch input and return the complete client result."""

        tool_call_id = await send_last_assistant_message(
            memory=self.memory,
            task_id=self.task_id,
            chat_id=self.chat_id,
            tool_name="patch",
        )
        return await self.file_editor.patch(
            mode=inputs.mode,
            path=inputs.path,
            old_string=inputs.old_string,
            new_string=inputs.new_string,
            replace_all=inputs.replace_all,
            patch=inputs.patch,
            tool_call_id=tool_call_id,
        )
