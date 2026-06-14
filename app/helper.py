from typing import Optional, List, Any, Union, Dict
from app.LLM.memory import Message, Memory
from app.Types.agent_types import ROLE_TYPE
from app.Tools.base_tool import get_current_tool_call_id, get_current_tool_input
from app.Task.task_manager import task_manager
from app.api.websocket_utils import send_ws_message
from app.DB.Queries.agent_event import create_agent_event
from pathlib import Path


import json


_sent_aura_thinking_batches: set[tuple[str, str, tuple[str, ...]]] = set()


def _get_tool_call_value(tool_call: Any, key: str, default: Any = None) -> Any:
    if isinstance(tool_call, dict):
        return tool_call.get(key, default)
    return getattr(tool_call, key, default)


def _get_tool_call_id(tool_call: Any) -> Optional[str]:
    return (
        _get_tool_call_value(tool_call, "tool_call_id")
        or _get_tool_call_value(tool_call, "id")
    )


def _get_tool_call_name(tool_call: Any) -> Optional[str]:
    return _get_tool_call_value(tool_call, "name")


def _normalize_tool_call(tool_call: Any) -> Optional[dict]:
    tool_call_id = _get_tool_call_id(tool_call)
    name = _get_tool_call_name(tool_call)
    if isinstance(tool_call, dict):
        input_value = (
            tool_call.get("input")
            if "input" in tool_call
            else tool_call.get("args", tool_call.get("arguments", {}))
        )
    else:
        input_value = (
            getattr(tool_call, "input", None)
            if hasattr(tool_call, "input")
            else getattr(tool_call, "args", getattr(tool_call, "arguments", {}))
        )

    if not tool_call_id and not name:
        return None

    return {
        "type": "tool_call",
        "id": tool_call_id,
        "tool_call_id": tool_call_id,
        "name": name,
        "input": input_value or {},
    }


def _normalize_for_compare(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _normalize_for_compare(val)
            for key, val in sorted(value.items())
            if val is not None
        }
    if isinstance(value, list):
        return [_normalize_for_compare(item) for item in value]
    return value


def _tool_inputs_match(left: Any, right: Any) -> bool:
    return _normalize_for_compare(left or {}) == _normalize_for_compare(right or {})


def _find_tool_call(
    tool_calls: Optional[List[Any]],
    *,
    tool_call_id: Optional[str] = None,
    tool_name: Optional[str] = None,
    tool_input: Optional[dict] = None,
) -> Optional[Any]:
    if not tool_calls:
        return None

    if tool_call_id:
        for tool_call in tool_calls:
            if _get_tool_call_id(tool_call) == tool_call_id:
                return tool_call

    if tool_name:
        named_tool_calls = [
            tool_call
            for tool_call in tool_calls
            if _get_tool_call_name(tool_call) == tool_name
        ]

        if tool_input is not None:
            for tool_call in named_tool_calls:
                normalized_tool_call = _normalize_tool_call(tool_call)
                if normalized_tool_call and _tool_inputs_match(
                    normalized_tool_call.get("input"),
                    tool_input,
                ):
                    return tool_call

        if len(named_tool_calls) == 1:
            return named_tool_calls[0]

    if tool_input is not None:
        input_matches = []
        for tool_call in tool_calls:
            normalized_tool_call = _normalize_tool_call(tool_call)
            if normalized_tool_call and _tool_inputs_match(
                normalized_tool_call.get("input"),
                tool_input,
            ):
                input_matches.append(tool_call)
        if len(input_matches) == 1:
            return input_matches[0]

    return None


def _tool_call_batch_key(tool_calls: Optional[List[Any]]) -> tuple[str, ...]:
    if not tool_calls:
        return ()

    batch_key = []
    for tool_call in tool_calls:
        tool_call_id = _get_tool_call_id(tool_call)
        if not tool_call_id:
            return ()
        batch_key.append(str(tool_call_id))
    return tuple(batch_key)


def _should_send_aura_thinking(
    *,
    task_id: str,
    chat_id: str,
    message_type: str,
    tool_calls: Optional[List[Any]],
) -> bool:
    if message_type != "aura_thinking":
        return True

    batch_key = _tool_call_batch_key(tool_calls)
    if len(batch_key) <= 1:
        return True

    sent_key = (task_id, chat_id, batch_key)
    if sent_key in _sent_aura_thinking_batches:
        return False

    _sent_aura_thinking_batches.add(sent_key)
    return True

# MEMORY - WIP
def update_memory(
        role: ROLE_TYPE,  # type: ignore
        content: Union[str, List[Any]],
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
        full_kwargs = {"base64_images": base64_images, **kwargs}
        memory.add_message(message_map[role](content, **full_kwargs))
    
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
            "type": "image",
            "image_url": f"data:image/jpeg;base64,{base64_image}"  # JPEG format make png
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
            # Find and update the text block that contains the query
            text_block = next((block for block in content if block.get("type") == "text" and "query:" in block.get("text", "")), None)
            if text_block:
                text_block["text"] += f"\nscreen_context: {parsed_screen_context}" if parsed_screen_context else ""

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

async def send_last_assistant_message(
    task_id: str, 
    chat_id: str, 
    memory: Optional[Memory], 
    tool_name: Optional[str] = None, 
    message_type: str = "aura_thinking",
    coming_from: str = "agent_callback_handler",
    tool_call_id: Optional[str] = None,
) -> Optional[str]:
    """Sends the Last role = assistant message to the client for displaying to the user.
    
    input:
        - memory(Optional[Memory]): Chat Memory,
        - task_id(str) : unique identifier for the task,
        - chat_id(str) : unique identifier for the chat,
        - tool_name(Optional[str]): name of the tool being called (if applicable),
        - message_type(str): "aura_thinking" or "aura_message",
        - coming_from(str): sender location identifier.
        - tool_call_id(Optional[str]): exact runtime tool call id, when known.

    returns:
        - tool_call_id(Optional[str]): the ID of the tool call matching tool_name.
    """
    try:
        runtime_tool_call_id = tool_call_id or get_current_tool_call_id()
        runtime_tool_input = get_current_tool_input()

        task_state = task_manager.get_state(task_id)
        websocket = task_state.websocket
        dbpool = task_state.dbpool

        if memory is None:
            return runtime_tool_call_id

        messages = memory.messages

        # Retrieve the last assistant message object directly to access its tool_calls
        last_assistant_obj = next((msg for msg in reversed(messages) if msg.role == "assistant" and getattr(msg, "tool_calls", None)), None)

        if last_assistant_obj:
            last_assistant = last_assistant_obj.to_dict()
            last_assistant_msg = last_assistant.get("message") or last_assistant.get("content")
            usage = last_assistant.get("usage")
            details = last_assistant.get("details")

            selected_tool_call = _find_tool_call(
                last_assistant_obj.tool_calls,
                tool_call_id=runtime_tool_call_id,
                tool_name=tool_name,
                tool_input=runtime_tool_input,
            )
            found_tool_call_id = (
                runtime_tool_call_id
                or _get_tool_call_id(selected_tool_call)
            )
            normalized_selected_tool_call = (
                _normalize_tool_call(selected_tool_call)
                if selected_tool_call
                else None
            )

            # Construct the payload
            payload = {
                "content": {
                    "role": "assistant",
                    "message": last_assistant_msg,
                    "usage": usage,
                    "details": details
                },
                "coming_from": coming_from
            }

            # Add tool_name only if it's aura_thinking
            if tool_name and message_type == "aura_thinking":
                payload["content"]["tool"] = tool_name
            if found_tool_call_id and message_type == "aura_thinking":
                payload["content"]["tool_call_id"] = found_tool_call_id
            if normalized_selected_tool_call and message_type == "aura_thinking":
                payload["content"]["current_tool_call"] = normalized_selected_tool_call

            should_send = _should_send_aura_thinking(
                task_id=task_id,
                chat_id=chat_id,
                message_type=message_type,
                tool_calls=last_assistant_obj.tool_calls,
            )
            if not should_send:
                return found_tool_call_id

            await send_ws_message(
                websocket=websocket,
                type=message_type,
                task_id=task_id,
                chat_id=chat_id,
                payload=payload
            )

            # Prepare common event payload
            event_payload = {
                "content": {
                    "message": last_assistant_msg,
                    "usage": usage,
                    "details": details
                }
            }
            if found_tool_call_id and message_type == "aura_thinking":
                event_payload["content"]["tool_call_id"] = found_tool_call_id
            if normalized_selected_tool_call and message_type == "aura_thinking":
                event_payload["content"]["current_tool_call"] = normalized_selected_tool_call

            # Insert AURA agent event in the DB
            event_kwargs = {
                "pool": dbpool,
                "task_id": task_id,
                "role": "assistant",
                "message_type": message_type,
                "payload": event_payload,
                "seq": task_state.get_next_seq()
            }
            
            # include tool only if aura_thinking
            if tool_name and message_type == "aura_thinking":
                event_kwargs["tool"] = tool_name

            await create_agent_event(**event_kwargs)
            
            return found_tool_call_id


    except Exception as e:
        print(f"Error while sending assistant message to the client: {e}")
    
    return tool_call_id or get_current_tool_call_id()

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
        # New task → append at the end with spacing and marker
        with open(file_path, "a", encoding="utf-8") as f:
            f.write("\n\n\n\n\n\n")  # 6 newlines for separation
            f.write("<------------- NEW TASK STARTS HERE --------------->\n")
            f.write(task_tag + "\n")
            f.write(entry)
            f.write(closing_tag + "\n")
