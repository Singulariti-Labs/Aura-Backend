from typing import Optional, List, Any, Union, Dict
from app.LLM.memory import Message, Memory
from app.Types.agent_types import ROLE_TYPE
from app.Task.task_manager import task_manager
from app.API.websocket_utils import send_ws_message
from pathlib import Path


import json

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

async def send_last_assistant_message(task_id: str, chat_id: str, memory: Optional[Memory], tool_name: Optional[str] = None):
    """Sends the Last role = assistant message to the client. for displaying user.
    
    input:
        - memory(Optional[Memory]): Chat Memory,
        - task_id(str) : unique identfier for the task,
        - chat_id(str) : unique identfier for the chat

    """
    try:

        task_state = task_manager.get_state(task_id)
        websocket = task_state.websocket

        messages = memory.messages

        last_assistant_msg = last_assistant_content = next((msg.content for msg in reversed(messages) if msg.role == "assistant"), None)

        # Save tool response
        save_tool_response(
            task_id=task_id,
            tool_name="assistant",
            response= json.dumps(last_assistant_content)
        )

        if last_assistant_msg:

            # this message to send the assistant message and to tell which tool we are going to call next in perticular step
            if tool_name:
                await send_ws_message(
                    websocket=websocket,
                    type="aura_message",
                    task_id=task_id,
                    chat_id=chat_id,
                    payload = {
                        "content": {
                            "role": "assistant",
                            "tool": tool_name,
                            "message": last_assistant_msg
                        }
                    }
            )
            
            else:
                await send_ws_message(
                    websocket=websocket,
                    type="aura_message",
                    task_id=task_id,
                    chat_id=chat_id,
                    payload = {
                        "content": {
                            "role": "assistant",
                            "message": last_assistant_msg
                        }
                    }
                )
    except Exception as e:
        print(f"Error while sending assistant message to the client: {e}")
        

def save_tool_response(task_id: str, tool_name: str, response: Union[Dict[str, Any], str]):
    """
    Save a tool's response to a file, grouped by task_id.

    Format:
    <task_id = "task_id">
    [tool_name] -> response/output
    </>
    """
    file_path = Path(__file__).parent.parent / "tools_output.txt"

    # Format the response: if it's not a string, convert it to JSON
    if not isinstance(response, str):
        try:
            response = json.dumps(response, ensure_ascii=False, indent=2)
        except Exception:
            response = str(response)

    entry = f'[ {tool_name} ] -> {response}\n'

    # Read existing content to check if task_id section exists
    content = ""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        pass

    task_tag = f'<task_id = "{task_id}">'
    closing_tag = '</>'

    if task_tag in content:
        # Append before the closing tag instead of after the task tag
        updated_content = content.replace(
            closing_tag, entry + closing_tag
        )
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(updated_content)
    else:
        # New task → append at the end
        with open(file_path, "a", encoding="utf-8") as f:
            f.write("\n\n\n\n" + task_tag + "\n")
            f.write(entry)
            f.write(closing_tag + "\n")