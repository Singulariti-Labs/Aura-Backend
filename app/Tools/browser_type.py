"""LangChain-facing wrapper for client-side browser typing."""

from typing import Optional

from langchain_core.language_models.chat_models import BaseChatModel

from app.Agentic_Tools.browser_tools import BrowserTools
from app.LLM.memory import Memory
from app.Tools.base_tool import BaseTool
from app.Types.agent_types import BrowserTypeToolInput
from app.helper import send_last_assistant_message


class BrowserTypeTool(BaseTool):
    """Provide the agent with the client-side ``browser_type`` action."""

    def __init__(
        self,
        llm: BaseChatModel,
        task_id: str,
        chat_id: str,
        memory: Optional[Memory] = None,
    ) -> None:
        """Initialize browser typing for the given task and chat.

        Args:
            llm: Chat model that can select and invoke this tool.
            task_id: Identifier of the active agent task.
            chat_id: Identifier of the chat associated with the browser session.
            memory: Optional conversation memory used by the tool bridge.
        """
        super().__init__(
            name="browser_type",
            description=(
                "Type text into an input field identified by its ref ID. Clears "
                "the field first, then types the new text. Requires "
                "browser_navigate and browser_snapshot to be called first."
            ),
            memory=memory,
            args_schema=BrowserTypeToolInput,
        )
        self.task_id = task_id
        self.chat_id = chat_id
        self.browser_tools = BrowserTools(
            llm=llm,
            task_id=task_id,
            chat_id=chat_id,
            memory=memory,
        )

    async def run(self, inputs: BrowserTypeToolInput):
        """Forward validated element and text inputs to the client browser.

        Args:
            inputs: Validated tool input containing the element ref and text.

        Returns:
            The browser typing result returned by the connected client.
        """
        tool_call_id = await send_last_assistant_message(
            memory=self.memory,
            task_id=self.task_id,
            chat_id=self.chat_id,
            tool_name="browser_type",
        )

        return await self.browser_tools.browser_type(
            ref=inputs.ref,
            text=inputs.text,
            tool_call_id=tool_call_id,
        )
