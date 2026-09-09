"""LangChain-facing native desktop computer-use tool.

All supported actions are native desktop actions; browser-specific
``cua_browser_*`` actions do not belong in this tool. The shared input contract
lives in :mod:`app.Types.agent_types`, matching the other Aura tools.
"""

from __future__ import annotations

from typing import Optional

from langchain_core.language_models.chat_models import BaseChatModel

from app.Agentic_Tools.computer_use_tool import ComputerUseToolExecutor
from app.LLM.memory import Memory
from app.Tools.base_tool import BaseTool
from app.Types.agent_types import ComputerUseInput
from app.helper import send_last_assistant_message


class ComputerUseTool(BaseTool):
    """Expose one native-only ``computer_use`` function to Aura."""

    def __init__(
        self,
        llm: BaseChatModel,
        task_id: str,
        chat_id: str,
        memory: Optional[Memory] = None,
    ) -> None:
        """Initialize the tool for the task's connected desktop client."""

        super().__init__(
            name="computer_use",
            description=(
                "Control native desktop applications on the user's computer via "
                "screenshots, accessibility elements, mouse, keyboard, scrolling, "
                "dragging, app discovery, and window targeting. Start with "
                "action='capture' and mode='som', then prefer returned element "
                "indexes over coordinates. Use app, pid, or window_id when a "
                "specific target is known. This tool contains native computer "
                "controls only; it does not support cua_browser_* actions."
            ),
            memory=memory,
            args_schema=ComputerUseInput,
        )
        self.task_id = task_id
        self.chat_id = chat_id
        self.executor = ComputerUseToolExecutor(
            llm=llm,
            task_id=task_id,
            chat_id=chat_id,
            memory=memory,
        )

    async def run(self, inputs: ComputerUseInput):
        """Delegate one validated native action to the client-side executor."""

        tool_call_id = await send_last_assistant_message(
            memory=self.memory,
            task_id=self.task_id,
            chat_id=self.chat_id,
            tool_name="computer_use",
        )

        return await self.executor.execute(
            input_params=inputs.model_dump(exclude_none=True),
            tool_call_id=tool_call_id,
        )
