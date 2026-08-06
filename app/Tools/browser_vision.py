"""LangChain-facing wrapper for client-side browser screenshot vision."""

from typing import Optional

from langchain_core.language_models.chat_models import BaseChatModel

from app.Agentic_Tools.browser_tools import BrowserTools
from app.LLM.memory import Memory
from app.Tools.base_tool import BaseTool
from app.Types.agent_types import BrowserVisionInput
from app.helper import send_last_assistant_message


class BrowserVisionTool(BaseTool):
    """Provide the agent with the client-side ``browser_vision`` action."""

    def __init__(
        self,
        llm: BaseChatModel,
        task_id: str,
        chat_id: str,
        memory: Optional[Memory] = None,
    ) -> None:
        """Initialize browser vision capture for the given task and chat.

        Args:
            llm: Chat model that can select and invoke this tool.
            task_id: Identifier of the active agent task.
            chat_id: Identifier of the chat associated with the browser session.
            memory: Optional conversation memory used by the tool bridge.
        """
        super().__init__(
            name="browser_vision",
            description=(
                "Take a screenshot of the current page so you can inspect it "
                "visually. Use this when you need to understand what the page "
                "looks like - especially for CAPTCHAs, visual verification "
                "challenges, complex layouts, or cases where the text snapshot "
                "misses important visual information. Returns screenshot_path "
                "and image_data_url so a vision-capable main model can inspect "
                "the screenshot. Requires browser_navigate to be called first."
            ),
            memory=memory,
            args_schema=BrowserVisionInput,
        )
        self.task_id = task_id
        self.chat_id = chat_id
        self.browser_tools = BrowserTools(
            llm=llm,
            task_id=task_id,
            chat_id=chat_id,
            memory=memory,
        )

    async def run(self, inputs: BrowserVisionInput):
        """Capture the browser page and return metadata plus native image data.

        Args:
            inputs: Validated visual question and optional annotation flag.

        Returns:
            The browser vision result returned by the connected client.
        """
        tool_call_id = await send_last_assistant_message(
            memory=self.memory,
            task_id=self.task_id,
            chat_id=self.chat_id,
            tool_name="browser_vision",
        )

        return await self.browser_tools.browser_vision(
            question=inputs.question,
            annotate=inputs.annotate,
            tool_call_id=tool_call_id,
        )
