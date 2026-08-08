"""Anthropic Messages API conversion and response parsing."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from app.LLM.model_bridge.common import (
    as_dict,
    is_remote_url,
    parse_data_url,
    text_from_blocks,
    tool_identity,
    tool_json_schema,
)


def _content_block(block: Dict[str, Any]) -> Dict[str, Any]:
    """Convert one canonical user/tool-result block to Anthropic content."""

    block_type = block.get("type")
    if block_type == "text":
        return {"type": "text", "text": str(block.get("text", ""))}

    if block_type == "image":
        media_type = str(block.get("media_type") or "image/png")
        value = str(block.get("image_url") or "")
        if is_remote_url(value):
            return {"type": "image", "source": {"type": "url", "url": value}}
        media_type, data = parse_data_url(value, media_type)
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data},
        }

    if block_type == "document":
        media_type = str(block.get("media_type") or "application/pdf")
        value = str(block.get("document_url") or "")
        if is_remote_url(value):
            return {"type": "document", "source": {"type": "url", "url": value}}
        media_type, data = parse_data_url(value, media_type)
        return {
            "type": "document",
            "source": {"type": "base64", "media_type": media_type, "data": data},
        }

    if block_type == "audio":
        raise ValueError(
            "Anthropic Messages does not accept the canonical audio block directly. "
            "Transcribe the audio before invoking an Anthropic model."
        )
    raise ValueError(f"Unsupported Anthropic content block type: {block_type!r}")


def anthropic_message_formater(
    messages: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Convert client history into Anthropic Messages API messages.

    Canonical assistant ``tool_call`` blocks become Anthropic ``tool_use``
    blocks. Consecutive canonical ``role=tool`` messages are combined into one
    Anthropic user message containing all corresponding ``tool_result`` blocks,
    which is required for reliable parallel tool calling.
    """

    formatted: List[Dict[str, Any]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        role = message.get("role")
        content = message.get("content", [])

        if role == "user":
            formatted.append(
                {"role": "user", "content": [_content_block(block) for block in content]}
            )
            index += 1
            continue

        if role == "assistant":
            assistant_content: List[Dict[str, Any]] = []
            for block in content:
                if block.get("type") == "text":
                    assistant_content.append(
                        {"type": "text", "text": str(block.get("text", ""))}
                    )
                elif block.get("type") == "tool_call":
                    assistant_content.append(
                        {
                            "type": "tool_use",
                            "id": block.get("tool_call_id"),
                            "name": block.get("name"),
                            "input": block.get("input") or {},
                        }
                    )
                else:
                    raise ValueError(
                        f"Unsupported canonical assistant block for Anthropic: {block.get('type')!r}"
                    )
            formatted.append({"role": "assistant", "content": assistant_content})
            index += 1
            continue

        if role == "tool":
            tool_results: List[Dict[str, Any]] = []
            vision_content: List[Dict[str, Any]] = []
            while index < len(messages) and messages[index].get("role") == "tool":
                tool_message = messages[index]
                content_blocks = tool_message.get("content", [])
                tool_content = content_blocks
                if tool_message.get("tool_name") == "browser_vision":
                    vision_prompts = [
                        block
                        for block in content_blocks
                        if block.get("type") == "text"
                        and str(block.get("text", "")).startswith(
                            "Analyze this browser screenshot and answer:"
                        )
                    ]
                    vision_media = [
                        block
                        for block in content_blocks
                        if block.get("type") != "text"
                    ]
                    tool_content = [
                        block
                        for block in content_blocks
                        if block not in vision_prompts and block not in vision_media
                    ]
                    vision_content.extend(
                        _content_block(block)
                        for block in [*vision_prompts, *vision_media]
                    )

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_message.get("tool_call_id"),
                        "content": [_content_block(block) for block in tool_content],
                        "is_error": bool(tool_message.get("is_error", False)),
                    }
                )
                index += 1
            formatted.append({"role": "user", "content": tool_results})
            if vision_content:
                formatted.append({"role": "user", "content": vision_content})
            continue

        raise ValueError(f"Unsupported canonical role for Anthropic: {role!r}")

    return formatted


def anthropic_tool_formater(tools: Optional[Sequence[Any]]) -> List[Dict[str, Any]]:
    """Convert LangChain structured tools to Anthropic client-tool schemas."""

    formatted: List[Dict[str, Any]] = []
    for tool in tools or []:
        name, description = tool_identity(tool)
        formatted.append(
            {
                "name": name,
                "description": description,
                "input_schema": tool_json_schema(tool),
            }
        )
    return formatted


def anthropic_response_formater(response: Any) -> Dict[str, Any]:
    """Normalize an Anthropic Message response into client history blocks."""

    content_blocks: List[Dict[str, Any]] = []
    native_content: List[Dict[str, Any]] = []
    for raw_block in getattr(response, "content", []) or []:
        block = as_dict(raw_block)
        native_content.append(block)
        block_type = block.get("type")
        if block_type == "text":
            content_blocks.append({"type": "text", "text": block.get("text", "")})
        elif block_type == "tool_use":
            content_blocks.append(
                {
                    "type": "tool_call",
                    "tool_call_id": block.get("id"),
                    "name": block.get("name"),
                    "input": block.get("input") or {},
                }
            )

    usage_raw = as_dict(getattr(response, "usage", None))
    input_tokens = int(usage_raw.get("input_tokens") or 0)
    output_tokens = int(usage_raw.get("output_tokens") or 0)
    usage = {
        "input": input_tokens,
        "output": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }
    message = {"role": "assistant", "content": content_blocks}
    return {
        "message": message,
        "tool_calls": [block for block in content_blocks if block.get("type") == "tool_call"],
        "text": text_from_blocks(content_blocks),
        "finish_reason": getattr(response, "stop_reason", None),
        "model": getattr(response, "model", None),
        "usage": usage,
        "native_message": {"role": "assistant", "content": native_content},
    }


async def invoke_anthropic_messages(
    *,
    llm: Any,
    model: str,
    system_prompt: str,
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    max_tokens: int,
) -> Any:
    """Invoke Anthropic's native asynchronous Messages API client."""

    client = getattr(llm, "_async_client", None)
    if client is None and hasattr(llm, "messages"):
        client = llm
    if client is None or not hasattr(client, "messages"):
        raise TypeError("The supplied LLM does not expose an Anthropic async Messages client.")

    request: Dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system_prompt:
        request["system"] = system_prompt
    if tools:
        request["tools"] = tools
        request["tool_choice"] = {"type": "auto"}
    return await client.messages.create(**request)
