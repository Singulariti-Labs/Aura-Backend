from typing import Optional
from langchain_core.language_models.chat_models import BaseChatModel

from app.Types.agent_types import GetAppContextInput
from app.Tools.base_tool import BaseTool
from app.LLM.memory import Memory
from app.Agentic_Tools.application_context_tool import GetAppContext
from app.helper import send_last_assistant_message

class GetAppContextTool(BaseTool):
    def __init__(self, llm: BaseChatModel, task_id: str, chat_id: str, memory: Optional[Memory] = None):
        super().__init__(
            name="get_app_context",
            description="""Gets the context of the application open on the screen by passing name, pid, hwnd, and exe_path.
            
            ## When To Use
            - Use it when the task is realted to application open or focused on the screen.`
            - Use this to get the deatils of the application open on the screen such as active_files, active_file_path, root_path, urls, etc
            """,
            memory=memory,
            args_schema=GetAppContextInput
        )
        self.memory = memory
        self.task_id = task_id
        self.chat_id = chat_id
        self.get_app_context_agentic = GetAppContext(llm=llm, task_id=task_id, chat_id=chat_id, memory=memory)

    async def run(self, inputs: GetAppContextInput):

        # Sending the last assistant message to the client.
        tool_call_id = await send_last_assistant_message(
            memory=self.memory, 
            task_id=self.task_id, 
            chat_id=self.chat_id, 
            tool_name="get_app_context"
        )
        
        name = inputs.name
        pid = inputs.pid
        hwnd = inputs.hwnd
        exe_path = inputs.exe_path
        
        response = await self.get_app_context_agentic.get_app_context(
            name=name, 
            pid=pid, 
            hwnd=hwnd, 
            exe_path=exe_path, 
            tool_call_id=tool_call_id
        )

        return response
