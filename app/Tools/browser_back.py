"""LangChain-facing wrapper for client-side browser history navigation."""

from typing import Optional

from langchain_core.language_models.chat_models import BaseChatModel

from app.Agentic_Tools.browser_tools import BrowserTools
from app.LLM.memory import Memory
from app.Tools.base_tool import BaseTool
from app.Types.agent_types import BrowserBackToolInput
from app.helper import send_last_assistant_message


class BrowserBackTool(BaseTool):
    """Provide the agent with the client-side ``browser_back`` action."""

    def __init__(
        self,
        llm: BaseChatModel,
        task_id: str,
        chat_id: str,
        memory: Optional[Memory] = None,
    ) -> None:
        """Initialize browser history navigation for the task and chat.

        Args:
            llm: Chat model that can select and invoke this tool.
            task_id: Identifier of the active agent task.
            chat_id: Identifier of the chat associated with the browser session.
            memory: Optional conversation memory used by the tool bridge.
        """
        super().__init__(
            name="browser_back",
            description=(
                "Navigate back to the previous page in browser history. Requires "
                "browser_navigate to be called first."
            ),
            memory=memory,
            args_schema=BrowserBackToolInput,
        )
        self.task_id = task_id
        self.chat_id = chat_id
        self.browser_tools = BrowserTools(
            llm=llm,
            task_id=task_id,
            chat_id=chat_id,
            memory=memory,
        )

    async def run(self, inputs: BrowserBackToolInput):
        """Request backward history navigation from the client browser.

        Args:
            inputs: Validated empty input object required by the tool interface.

        Returns:
            The browser history navigation result returned by the client.
        """
        tool_call_id = await send_last_assistant_message(
            memory=self.memory,
            task_id=self.task_id,
            chat_id=self.chat_id,
            tool_name="browser_back",
        )

        return await self.browser_tools.browser_back(
            tool_call_id=tool_call_id,
        )
