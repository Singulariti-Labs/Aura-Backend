"""LangChain-facing wrapper for client-side browser console access."""

from typing import Optional

from langchain_core.language_models.chat_models import BaseChatModel

from app.Agentic_Tools.browser_tools import BrowserTools
from app.LLM.memory import Memory
from app.Tools.base_tool import BaseTool
from app.Types.agent_types import BrowserConsoleToolInput
from app.helper import send_last_assistant_message


class BrowserConsoleTool(BaseTool):
    """Provide the agent with the client-side ``browser_console`` action."""

    def __init__(
        self,
        llm: BaseChatModel,
        task_id: str,
        chat_id: str,
        memory: Optional[Memory] = None,
    ) -> None:
        """Initialize browser console access for the given task and chat.

        Args:
            llm: Chat model that can select and invoke this tool.
            task_id: Identifier of the active agent task.
            chat_id: Identifier of the chat associated with the browser session.
            memory: Optional conversation memory used by the tool bridge.
        """
        super().__init__(
            name="browser_console",
            description=(
                "Get browser console output and JavaScript errors from the current "
                "page. Returns console.log/warn/error/info messages and uncaught JS "
                "exceptions. Use this to detect silent JavaScript errors, failed "
                "API calls, and application warnings. Requires browser_navigate to "
                "be called first. When 'expression' is provided, evaluates "
                "JavaScript in the page context and returns the result \u2014 use this "
                "for DOM inspection, reading page state, or extracting data "
                "programmatically."
            ),
            memory=memory,
            args_schema=BrowserConsoleToolInput,
        )
        self.task_id = task_id
        self.chat_id = chat_id
        self.browser_tools = BrowserTools(
            llm=llm,
            task_id=task_id,
            chat_id=chat_id,
            memory=memory,
        )

    async def run(self, inputs: BrowserConsoleToolInput):
        """Forward console-reading or expression-evaluation inputs to the client.

        Args:
            inputs: Validated optional clear flag and JavaScript expression.

        Returns:
            The console or expression result returned by the connected client.
        """
        tool_call_id = await send_last_assistant_message(
            memory=self.memory,
            task_id=self.task_id,
            chat_id=self.chat_id,
            tool_name="browser_console",
        )

        return await self.browser_tools.browser_console(
            clear=inputs.clear,
            expression=inputs.expression,
            tool_call_id=tool_call_id,
        )
