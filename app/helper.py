from typing import Optional, List

from app.LLM.memory import Message, Memory
from app.Types.agent_types import ROLE_TYPE

# MEMORY - WIP
def update_memory(
        role: ROLE_TYPE,  # type: ignore
        content: str,
        memory: Memory,
        base64_images: Optional[List[str]] = None,
        **kwargs,
) -> None:
        """
        Adds a message to the agent’s memory based on the sender role.

        Input:
            role (ROLE_TYPE): The message sender role (user, system, assistant, tool).
            content (str): Message text content.
            base64_images (Optional[List[str]]): Base64 encoded screenshot, if any.
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
        kwargs = {"base64_images": base64_images, **(kwargs if role == "tool" else {})}
        memory.add_message(message_map[role](content, **kwargs))
    
def update_input_messages_with_screenshot_and_context(
    input_message: List[dict],
    base64_image: Optional[str] = None,
    parsed_screen_context: Optional[str] = None
) -> List[dict]:
    """
    Updates input_messages (OpenAI format) to:
    - If last message is from user:
        - Append base64_image as an image_url object
        - Modify the existing 'query' text to include current_state (if any)
    - If last message is NOT from user:
        - Add a new user message with default screenshot caption and image (and current_state if any)

    Returns:
        Modified input_messages list
    """

    last_message = input_message[-1] if input_message else None

    # Construct the updated message parts
    image_part = (
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{base64_image}"  # JPEG format make png
            }
        } if base64_image else None
    )

    default_text = "query: This is the updated screenshot after performing the actions."
    if parsed_screen_context:
        default_text += f"\nscreen_context: {parsed_screen_context}"

    # CASE 1: Last message is user, and content is list (OpenAI multimodal format)
    if last_message and last_message.get("role") == "user":
        content = last_message.get("content", [])

        # Ensure content is a list
        if isinstance(content, list):
            # Update the first text block (assuming it's the query)
            if content and content[0]["type"] == "text" and "query:" in content[0]["text"]:
                content[0]["text"] += f"\nscreen_context: {parsed_screen_context}" if parsed_screen_context else ""

            # Append the image if provided
            if image_part:
                content.append(image_part)

            last_message["content"] = content

        input_message[-1] = last_message

    else:
        # CASE 2: Last message is not user, append new user message
        new_user_message = {
            "role": "user",
            "content": [{"type": "text", "text": default_text}]
        }

        if image_part:
            new_user_message["content"].append(image_part)

        input_message.append(new_user_message)

    return input_message