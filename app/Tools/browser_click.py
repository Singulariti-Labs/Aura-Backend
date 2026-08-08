"""LangChain-facing wrapper for client-side browser clicks."""

from typing import Optional

from langchain_core.language_models.chat_models import BaseChatModel

from app.Agentic_Tools.browser_tools import BrowserTools
from app.LLM.memory import Memory
from app.Tools.base_tool import BaseTool
from app.Types.agent_types import BrowserClickToolInput
from app.helper import send_last_assistant_message


class BrowserClickTool(BaseTool):
    """Provide the agent with the client-side ``browser_click`` action."""

    def __init__(
        self,
        llm: BaseChatModel,
        task_id: str,
        chat_id: str,
        memory: Optional[Memory] = None,
    ) -> None:
        """Initialize browser clicks for the given task and chat.

        Args:
            llm: Chat model that can select and invoke this tool.
            task_id: Identifier of the active agent task.
            chat_id: Identifier of the chat associated with the browser session.
            memory: Optional conversation memory used by the tool bridge.
        """
        super().__init__(
            name="browser_click",
            description=(
                "Click on an element identified by its ref ID from the snapshot "
                "(e.g., '@e5'). The ref IDs are shown in square brackets in the "
                "snapshot output. Requires browser_navigate and browser_snapshot "
                "to be called first."
            ),
            memory=memory,
            args_schema=BrowserClickToolInput,
        )
        self.task_id = task_id
        self.chat_id = chat_id
        self.browser_tools = BrowserTools(
            llm=llm,
            task_id=task_id,
            chat_id=chat_id,
            memory=memory,
        )

    async def run(self, inputs: BrowserClickToolInput):
        """Forward a validated element reference to the client browser.

        Args:
            inputs: Validated tool input containing the element reference.

        Returns:
            The browser click result returned by the connected client.
        """
        # Send the pending assistant tool call to the client and recover its ID.
        tool_call_id = await send_last_assistant_message(
            memory=self.memory,
            task_id=self.task_id,
            chat_id=self.chat_id,
            tool_name="browser_click",
        )

        return await self.browser_tools.browser_click(
            ref=inputs.ref,
            tool_call_id=tool_call_id,
        )
