"""LangChain-facing wrapper for client-side durable-memory updates."""

from typing import Optional

from langchain_core.language_models.chat_models import BaseChatModel

from app.Agentic_Tools.memory_tools import MemoryTools
from app.LLM.memory import Memory
from app.Tools.base_tool import BaseTool
from app.Types.agent_types import MemoryUpdateToolInput
from app.helper import send_last_assistant_message


MEMORY_UPDATE_TOOL_DESCRIPTION = (
    """Update durable facts in persistent memory files.
    Memory is stored in named markdown files. The `name` field selects the memory
    file without the `.md` suffix.

    Examples:
    - name="preference" updates preference.md
    - name="aura" updates aura.md
    - name="current-project" updates current-project.md

    Use `target` to choose the memory category:
    - "user": facts about the user, preferences, identity, communication style
    - "memory": assistant/project notes, environment facts, conventions, tool quirks,
      durable lessons, working projects

    Actions:
    - add: add a new memory entry
    - replace: replace an existing memory entry using `old_text`
    - remove: remove an existing memory entry using `old_text`
    Note: for replace & remove actions please provide complete
    memory fact text as old_string to avoide mattching error.

    For add:
    - requires `name`
    - requires `content` or `new_text`

    For replace:
    - requires `name`
    - requires `old_text`
    - requires `content` or `new_text`

    For remove:
    - requires `name`
    - requires `old_text`

    Use `description` only when the description of the memory file itself should be
    changed. Do not include `description` for normal memory entry updates.

    The best memory stops the user repeating themselves.

    Use `operations` for batch updates. Batch updates are applied atomically.
    When using `operations`, omit top-level `action`,`content`,
    `old_text`, and `new_text`. Each operation includes its own `name`.

    Do not save temporary task progress, logs, one-off IDs, or short-lived details.
    Save only durable facts that should be useful in future sessions 

    ** Each Fact Should Be:**
        - short, clear, and independently useful
        - written as a declarative statement, not an instruction to the assistant
        - stable enough to remain useful across future sessions
        - specific enough to prevent the user from repeating themselves
    

    **SKIP This Info as Facts:**
        trivial/obvious info
        easily re-discovered facts
        raw data dumps
        task progress
        completed-work logs
        temporary TODO state
    """
)


class MemoryUpdateTool(BaseTool):
    """Provide the agent with the client-side ``memory_update`` action."""

    def __init__(
        self,
        llm: BaseChatModel,
        task_id: str,
        chat_id: str,
        memory: Optional[Memory] = None,
    ) -> None:
        """Initialize durable-memory updates for the given task and chat."""

        super().__init__(
            name="memory_update",
            description=MEMORY_UPDATE_TOOL_DESCRIPTION,
            memory=memory,
            args_schema=MemoryUpdateToolInput,
        )
        self.task_id = task_id
        self.chat_id = chat_id
        self.memory_tools = MemoryTools(
            llm=llm,
            task_id=task_id,
            chat_id=chat_id,
            memory=memory,
        )

    async def run(self, inputs: MemoryUpdateToolInput):
        """Forward validated update input and return the complete client result."""

        tool_call_id = await send_last_assistant_message(
            memory=self.memory,
            task_id=self.task_id,
            chat_id=self.chat_id,
            tool_name="memory_update",
        )
        operations = (
            [operation.model_dump(exclude_none=True) for operation in inputs.operations]
            if inputs.operations is not None
            else None
        )
        return await self.memory_tools.memory_update(
            name=inputs.name,
            target=inputs.target,
            action=inputs.action,
            description=inputs.description,
            content=inputs.content,
            new_text=inputs.new_text,
            old_text=inputs.old_text,
            operations=operations,
            tool_call_id=tool_call_id,
        )
