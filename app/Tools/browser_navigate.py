"""LangChain-facing wrapper for client-side browser navigation."""

from typing import Optional

from langchain_core.language_models.chat_models import BaseChatModel

from app.Agentic_Tools.browser_tools import BrowserTools
from app.LLM.memory import Memory
from app.Tools.base_tool import BaseTool
from app.Types.agent_types import BrowserNavigateToolInput
from app.helper import send_last_assistant_message


class BrowserNavigateTool(BaseTool):
    """Provide the agent with the client-side ``browser_navigate`` action."""

    def __init__(
        self,
        llm: BaseChatModel,
        task_id: str,
        chat_id: str,
        memory: Optional[Memory] = None,
    ) -> None:
        """Initialize browser navigation for the given task and chat.

        Args:
            llm: Chat model that can select and invoke this tool.
            task_id: Identifier of the active agent task.
            chat_id: Identifier of the chat associated with the browser session.
            memory: Optional conversation memory used by the tool bridge.
        """
        super().__init__(
            name="browser_navigate",
            description=(
                "Navigate to a URL in the browser. Initializes the session and "
                "loads the page. Must be called before other browser tools. For "
                "plain-text endpoints — URLs ending in .md, .txt, .json, .yaml, "
                ".yml, .csv, .xml, raw.githubusercontent.com, or any documented "
                "API endpoint — prefer curl via the terminal tool or web_extract; "
                "the browser stack is overkill and much slower for these. Use "
                "browser tools when you need to interact with a page (click, fill "
                "forms, dynamic content). Returns a compact page snapshot with "
                "interactive elements and ref IDs — no need to call "
                "browser_snapshot separately after navigating."
            ),
            memory=memory,
            args_schema=BrowserNavigateToolInput,
        )
        self.task_id = task_id
        self.chat_id = chat_id
        self.browser_tools = BrowserTools(
            llm=llm,
            task_id=task_id,
            chat_id=chat_id,
            memory=memory,
        )

    async def run(self, inputs: BrowserNavigateToolInput):
        """Forward a validated navigation request to the client browser.

        Args:
            inputs: Validated tool input containing the target URL.

        Returns:
            The browser navigation result returned by the connected client.
        """
        # Send the pending assistant tool call to the client and recover its ID.
        tool_call_id = await send_last_assistant_message(
            memory=self.memory,
            task_id=self.task_id,
            chat_id=self.chat_id,
            tool_name="browser_navigate",
        )

        return await self.browser_tools.browser_navigate(
            url=inputs.url,
            tool_call_id=tool_call_id,
        )
