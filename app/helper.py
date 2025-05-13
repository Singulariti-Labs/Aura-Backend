from typing import Optional

from app.LLM.memory import Message, Memory
from app.Types.agent_types import ROLE_TYPE

# MEMORY - WIP
def update_memory(
        self,
        role: ROLE_TYPE,  # type: ignore
        content: str,
        memory: Memory,
        base64_image: Optional[str] = None,
        **kwargs,
) -> None:
        """
        Adds a message to the agent’s memory based on the sender role.

        Input:
            role (ROLE_TYPE): The message sender role (user, system, assistant, tool).
            content (str): Message text content.
            base64_image (Optional[str]): Base64 encoded screenshot, if any.
            **kwargs: Additional metadata such as tool_call_id.

        Raises:
            ValueError: If the provided role is unsupported.
        """
        message_map = {
            "user": Message.user_message,
            "system": Message.system_message,
            "assistant": Message.assistant_message,
            "tool": lambda content, **kw: Message.tool_message(content, **kw),
        }

        if role not in message_map:
            raise ValueError(f"Unsupported message role: {role}")

        # Create message with appropriate parameters based on role
        kwargs = {"base64_image": base64_image, **(kwargs if role == "tool" else {})}
        memory.add_message(message_map[role](content, **kwargs))