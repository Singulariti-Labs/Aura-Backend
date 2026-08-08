"""LangChain-facing wrapper for client-side browser snapshots."""

from typing import Optional

from langchain_core.language_models.chat_models import BaseChatModel

from app.Agentic_Tools.browser_tools import BrowserTools
from app.LLM.memory import Memory
from app.Tools.base_tool import BaseTool
from app.Types.agent_types import BrowserSnapshotToolInput
from app.helper import send_last_assistant_message


class BrowserSnapshotTool(BaseTool):
    """Provide the agent with the client-side ``browser_snapshot`` action."""

    def __init__(
        self,
        llm: BaseChatModel,
        task_id: str,
        chat_id: str,
        memory: Optional[Memory] = None,
    ) -> None:
        """Initialize browser snapshots for the given task and chat.

        Args:
            llm: Chat model that can select and invoke this tool.
            task_id: Identifier of the active agent task.
            chat_id: Identifier of the chat associated with the browser session.
            memory: Optional conversation memory used by the tool bridge.
        """
        super().__init__(
            name="browser_snapshot",
            description=(
                "Get a text-based snapshot of the current page's accessibility "
                "tree. Returns interactive elements with ref IDs (like @e1, @e2) "
                "for browser_click and browser_type. full=false (default): compact "
                "view with interactive elements. full=true: complete page content. "
                "Snapshots over 8000 chars are truncated. Requires browser_navigate "
                "first. Note: browser_navigate already returns a compact snapshot "
                "\u2014 use this to refresh after interactions that change the page, "
                "or with full=true for complete content."
            ),
            memory=memory,
            args_schema=BrowserSnapshotToolInput,
        )
        self.task_id = task_id
        self.chat_id = chat_id
        self.browser_tools = BrowserTools(
            llm=llm,
            task_id=task_id,
            chat_id=chat_id,
            memory=memory,
        )

    async def run(self, inputs: BrowserSnapshotToolInput):
        """Forward a validated snapshot request to the client browser.

        Args:
            inputs: Validated tool input containing the optional full-page flag.

        Returns:
            The browser snapshot result returned by the connected client.
        """
        # Send the pending assistant tool call to the client and recover its ID.
        tool_call_id = await send_last_assistant_message(
            memory=self.memory,
            task_id=self.task_id,
            chat_id=self.chat_id,
            tool_name="browser_snapshot",
        )

        return await self.browser_tools.browser_snapshot(
            full=inputs.full,
            tool_call_id=tool_call_id,
        )
