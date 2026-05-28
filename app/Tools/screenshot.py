from typing import Optional
from langchain_core.language_models.chat_models import BaseChatModel

from app.Tools.base_tool import BaseTool
from app.LLM.memory import Memory
from app.Types.agent_types import ScreenshotToolInput
from app.helper import send_last_assistant_message
from app.Agentic_Tools.screenshot_tool import ScreenshotToolExecutor

class ScreenshotTool(BaseTool):
    def __init__(self, llm: BaseChatModel, task_id: str, chat_id: str, memory: Optional[Memory] = None, llm_provider: Optional[str] = None):
        """
        Initializes the Screenshot Tool with a language model and optional memory.
        """
        super().__init__(
            name="screenshot",
            description="""Use this tool to capture a screenshot of the user's current active screen.
            
            ## When To Use
            - Use this tool when the user asks a question about the active application, window, or whatever is visible on their screen.
            - Examples of user queries requiring screen context:
              - "Can you tell me how to save this file?"
              - "What am I looking at?"
              - "What app is this?"
              - "What is this showing on my screen?"
              - "Which place is this?"
              - "What does this error mean?"
              - Any questions requiring visual context or understanding of what is currently on the screen.
            """,
            memory=memory,
            args_schema=ScreenshotToolInput
        )
        self.chat_id = chat_id
        self.task_id = task_id
        self.llm_provider = llm_provider
        self.executor = ScreenshotToolExecutor(llm=llm, task_id=task_id, chat_id=chat_id, memory=memory)

    async def run(self, inputs: ScreenshotToolInput):
        """
        Executes the screenshot capturing logic.
        """
        # Sending the last assistant message and retrieving the correct tool_call_id
        tool_call_id = await send_last_assistant_message(
            memory=self.memory, 
            task_id=self.task_id, 
            chat_id=self.chat_id, 
            tool_name="screenshot"
        )
        
        reason = inputs.reason
        
        response = await self.executor.capture_screenshot(
            reason=reason,
            tool_call_id=tool_call_id,
            llm_provider=self.llm_provider
        )
        return response
