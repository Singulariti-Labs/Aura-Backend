from typing import Optional
import uuid

from app.Types.agent_types import WebScraperInput
from app.Tools.base_tool import BaseTool
from app.LLM.memory import Memory
from app.Agentic_Tools.web_search import web_scraper
from app.helper import send_last_assistant_message

class WebScraperTool(BaseTool):
    def __init__(self, task_id: str, chat_id: str, memory: Optional[Memory] = None):
        super().__init__(
            name="web_scraper",
            description="Performs the web scraping for the given urls if the content from web search is not sufficient to provide indetail information.",
            memory=memory,
            args_schema=WebScraperInput
        )
        self.memory = memory
        self.task_id = task_id
        self.chat_id = chat_id

    async def run(self, inputs: WebScraperInput):

        # Sending the last assistant message to the client.
        await send_last_assistant_message(memory=self.memory, task_id=self.task_id, chat_id=self.chat_id, tool_name="web_scraping")
        urls_string = inputs.urls_string
        workspace_path = inputs.workspace_path
        chat_name = inputs.chat_name
        tool_call_id = str(uuid.uuid4())

        response = await web_scraper(urls_string=urls_string, workspace_path=workspace_path, chat_name=chat_name, task_id=self.task_id, chat_id=self.chat_id, memory=self.memory, tool_call_id=tool_call_id)

        return response