"""LangChain-facing wrapper for client-side browser scrolling."""

from typing import Optional

from langchain_core.language_models.chat_models import BaseChatModel

from app.Agentic_Tools.browser_tools import BrowserTools
from app.LLM.memory import Memory
from app.Tools.base_tool import BaseTool
from app.Types.agent_types import BrowserScrollToolInput
from app.helper import send_last_assistant_message


class BrowserScrollTool(BaseTool):
    """Provide the agent with the client-side ``browser_scroll`` action."""

    def __init__(
        self,
        llm: BaseChatModel,
        task_id: str,
        chat_id: str,
        memory: Optional[Memory] = None,
    ) -> None:
        """Initialize browser scrolling for the given task and chat.

        Args:
            llm: Chat model that can select and invoke this tool.
            task_id: Identifier of the active agent task.
            chat_id: Identifier of the chat associated with the browser session.
            memory: Optional conversation memory used by the tool bridge.
        """
        super().__init__(
            name="browser_scroll",
            description=(
                "Scroll the page in a direction. Use this to reveal more content "
                "that may be below or above the current viewport. Requires "
                "browser_navigate to be called first."
            ),
            memory=memory,
            args_schema=BrowserScrollToolInput,
        )
        self.task_id = task_id
        self.chat_id = chat_id
        self.browser_tools = BrowserTools(
            llm=llm,
            task_id=task_id,
            chat_id=chat_id,
            memory=memory,
        )

    async def run(self, inputs: BrowserScrollToolInput):
        """Forward a validated scroll direction to the client browser.

        Args:
            inputs: Validated tool input containing ``up`` or ``down``.

        Returns:
            The browser scroll result returned by the connected client.
        """
        tool_call_id = await send_last_assistant_message(
            memory=self.memory,
            task_id=self.task_id,
            chat_id=self.chat_id,
            tool_name="browser_scroll",
        )

        return await self.browser_tools.browser_scroll(
            direction=inputs.direction,
            tool_call_id=tool_call_id,
        )
