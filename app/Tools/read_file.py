from typing import Optional
from langchain_core.language_models.chat_models import BaseChatModel

from app.Tools.base_tool import BaseTool
from app.LLM.memory import Memory
from app.Types.agent_types import ReadFileToolInput
from app.helper import send_last_assistant_message
from app.Agentic_Tools.file_editor import FileEditor

class ReadFileTool(BaseTool):
    def __init__(self, llm: BaseChatModel, task_id: str, chat_id: str,  memory: Optional[Memory] = None, llm_provider: Optional[str] = None):
        """
        Initializes the Read File Tool with a language model and optional memory.

        Input:
        - llm: A chat-based LLM instance that powers the SupervisorAgent.
        - task_id: A unique identifier for a task.
        - chat_id: A unique identifier for a chat.
        - memory: Optional memory object to retain conversation history/context.
        """
        super().__init__(
            name="read_file",
            description="""Use this tool to read any file, document or directory. Supports 
            text, code, docx, xlsx, pptx, pdf, images, csv, zip. Use offset and limit to 
            paginate large files or to read specific section.

            ## Pagination
            - You got what you needed — stop, do NOT call again
            - Footer says end — file is fully read, stop:
              (End of file — N lines)
            - Footer says truncated AND you need more content — call again with suggested offset:
              (Showing lines 1–2000 of 5400. Use offset=2001 to continue.)
            - Never paginate just because the file is truncated,
              only paginate if you actually need the remaining content.

            ## Limits Per Read Tool Call
            - Text / code / docx / xlsx / csv : 2000 lines per read, 50KB max per chunk
            - PPTX                            : 50 slides per read
            - Images                          : 4MB max file size
            - PDF                             : 70 pages max
            - XLSX                            : 10,000 rows per sheet
            - ZIP                             : lists contents only, does not extract

            ## Usage Guildlines
            - To read later sections of a large file, call the tool again with a larger offset.
            - Any line longer than 2000 characters is truncated. The total output is also capped (typically at 50 KB).
            - The tool can read image files (JPEG, PNG, GIF, WebP) and PDFs, returning them as attachments.
            """,
            memory=memory,
            args_schema=ReadFileToolInput
        )
        self.chat_id = chat_id
        self.task_id = task_id
        self.llm_provider = llm_provider
        self.file_editor = FileEditor(llm=llm, task_id=task_id, chat_id=chat_id, memory=memory)

    async def run(self, inputs: ReadFileToolInput):
        """
        Executes the read_file logic.
        """
        # Sending the last assistant message and retrieving the correct tool_call_id
        tool_call_id = await send_last_assistant_message(
            memory=self.memory, 
            task_id=self.task_id, 
            chat_id=self.chat_id, 
            tool_name="read_file"
        )
        
        filePath = inputs.filePath
        offset = inputs.offset
        limit = inputs.limit
        
        response = await self.file_editor.read_file(
            filePath=filePath,
            offset=offset,
            limit=limit,
            tool_call_id=tool_call_id,
            llm_provider=self.llm_provider
        )
        return response
