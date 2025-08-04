from typing import Optional
import uuid

from app.Types.agent_types import WebSearchInput
from app.Tools.base_tool import BaseTool
from app.LLM.memory import Memory
from app.Agentic_Tools.web_search import web_search
from app.helper import send_last_assistant_message

class WebSearchTool(BaseTool):
    def __init__(self, task_id: str, chat_id: str, memory: Optional[Memory] = None):
        super().__init__(
            name="web_search",
            description="Performs the web search for the given query.",
            memory=memory,
            args_schema=WebSearchInput
        )
        self.memory = memory
        self.task_id = task_id
        self.chat_id = chat_id

    async def run(self, inputs: WebSearchInput) -> str:

        # Sending the last assistant message to the client.
        await send_last_assistant_message(memory=self.memory, task_id=self.task_id, chat_id=self.chat_id, tool_name="web_search")
        query = inputs.query
        num_results = inputs.num_results
        tool_call_id = str(uuid.uuid4())

        response = await web_search(query=query, num_results=num_results, task_id=self.task_id, chat_id=self.chat_id, memory=self.memory, tool_call_id=tool_call_id)

        return response