from __future__ import annotations

import json
import re
from typing import Any, List, Optional, Sequence, Tuple

from langchain_core.agents import AgentAction
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage


def format_multimodal_tool_messages(
    intermediate_steps: Sequence[Tuple[AgentAction, Any]],
    provider: Optional[str] = None,
) -> List[BaseMessage]:
    """
    Convert agent intermediate steps into messages without stringifying screenshot images.

    LangChain's default formatter JSON-serializes non-string tool observations. That is
    fine for normal tools, but it turns screenshot base64 into text tokens. For the
    screenshot tool, we convert the observation into native multimodal message blocks.
    """
    messages: List[BaseMessage] = []
    provider_name = (provider or "generic").lower()

    for agent_action, observation in intermediate_steps:
        if _is_tool_agent_action(agent_action):
            new_messages = list(getattr(agent_action, "message_log", []) or [])
            new_messages.extend(
                _create_tool_messages(agent_action, observation, provider_name)
            )
            messages.extend([message for message in new_messages if message not in messages])
        else:
            messages.append(AIMessage(content=getattr(agent_action, "log", "")))

    return messages


def _is_tool_agent_action(agent_action: AgentAction) -> bool:
    return bool(
        getattr(agent_action, "tool_call_id", None)
        or getattr(agent_action, "message_log", None)
    )


def _create_tool_messages(
    agent_action: AgentAction,
    observation: Any,
    provider: str,
) -> List[BaseMessage]:
    tool_name = getattr(agent_action, "tool", "")
    tool_call_id = _tool_call_id(agent_action, observation)

    if tool_name != "screenshot":
        if isinstance(observation, ToolMessage):
            return [observation]
        return [
            ToolMessage(
                tool_call_id=tool_call_id,
                content=_default_tool_content(observation),
                name=tool_name,
            )
        ]

    screenshot = _extract_screenshot_observation(observation)
    if not screenshot or not screenshot.get("image_base64"):
        return [
            ToolMessage(
                tool_call_id=tool_call_id,
                content=_default_tool_content(observation),
                name=tool_name,
            )
        ]

    text = screenshot.get("output") or "Screenshot captured successfully."
    image_block = {
        "type": "image",
        "source_type": "base64",
        "mime_type": screenshot.get("mime_type") or "image/png",
        "data": screenshot["image_base64"],
    }

    if provider in {"openai", "azure_openai", "azure", "open_router", "agent_router"}:
        return [
            ToolMessage(
                tool_call_id=tool_call_id,
                content=text,
                name=tool_name,
            ),
            HumanMessage(
                content=[
                    {"type": "text", "text": "Screenshot captured successfully."},
                    image_block,
                ]
            ),
        ]

    return [
        ToolMessage(
            tool_call_id=tool_call_id,
            content=[
                {"type": "text", "text": text},
                image_block,
            ],
            name=tool_name,
        )
    ]


def _tool_call_id(agent_action: AgentAction, observation: Any) -> str:
    if isinstance(observation, ToolMessage) and observation.tool_call_id:
        return observation.tool_call_id
    return str(getattr(agent_action, "tool_call_id", "") or getattr(agent_action, "id", ""))


def _default_tool_content(observation: Any) -> str:
    if isinstance(observation, ToolMessage):
        return (
            observation.content
            if isinstance(observation.content, str)
            else json.dumps(observation.content, ensure_ascii=False)
        )
    if isinstance(observation, str):
        return observation
    try:
        return json.dumps(observation, ensure_ascii=False)
    except TypeError:
        return str(observation)


def _extract_screenshot_observation(observation: Any) -> Optional[dict]:
    payload = _observation_payload(observation)
    if not isinstance(payload, dict):
        return None

    image_base64 = (
        payload.get("image_base64")
        or payload.get("base64_image")
        or payload.get("base64")
    )
    mime_type = payload.get("mime_type") or payload.get("mime") or "image/png"

    if not image_base64:
        image_base64, mime_type = _extract_from_images(payload.get("images"), mime_type)

    if not image_base64:
        image_base64, mime_type = _extract_from_legacy_result(payload.get("result"), mime_type)

    if not image_base64:
        return None

    return {
        "image_base64": image_base64,
        "mime_type": mime_type,
        "output": payload.get("output") or "Screenshot captured successfully.",
    }


def _observation_payload(observation: Any) -> Any:
    if isinstance(observation, ToolMessage):
        content = observation.content
    else:
        content = observation

    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return None
    return content


def _extract_from_images(images: Any, fallback_mime: str) -> tuple[Optional[str], str]:
    if not isinstance(images, list):
        return None, fallback_mime

    for image in images:
        if not isinstance(image, dict):
            continue
        image_base64 = (
            image.get("image_base64")
            or image.get("base64_image")
            or image.get("content")
            or image.get("data")
            or image.get("base64")
        )
        if image_base64:
            return image_base64, image.get("mime_type") or image.get("mime") or fallback_mime

    return None, fallback_mime


def _extract_from_legacy_result(result: Any, fallback_mime: str) -> tuple[Optional[str], str]:
    if not isinstance(result, dict):
        return None, fallback_mime

    content = result.get("content")
    if not isinstance(content, list):
        return None, fallback_mime

    for block in content:
        image_base64, mime_type = _extract_from_content_block(block, fallback_mime)
        if image_base64:
            return image_base64, mime_type

    return None, fallback_mime


def _extract_from_content_block(block: Any, fallback_mime: str) -> tuple[Optional[str], str]:
    if not isinstance(block, dict):
        return None, fallback_mime

    if block.get("type") == "tool_result":
        nested = block.get("content")
        if isinstance(nested, list):
            for item in nested:
                image_base64, mime_type = _extract_from_content_block(item, fallback_mime)
                if image_base64:
                    return image_base64, mime_type

    source = block.get("source")
    if block.get("type") == "image" and isinstance(source, dict):
        data = source.get("data")
        if data:
            return data, source.get("media_type") or fallback_mime

    image_url = block.get("image_url")
    if isinstance(image_url, dict):
        return _extract_data_uri(image_url.get("url"), fallback_mime)

    return None, fallback_mime


def _extract_data_uri(value: Any, fallback_mime: str) -> tuple[Optional[str], str]:
    if not isinstance(value, str):
        return None, fallback_mime

    match = re.match(r"^data:(?P<mime>image/[^;]+);base64,(?P<data>.+)$", value)
    if not match:
        return None, fallback_mime

    return match.group("data"), match.group("mime")
