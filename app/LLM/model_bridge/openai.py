"""OpenAI Chat Completions conversion and response parsing."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

from app.LLM.model_bridge.common import (
    as_dict,
    parse_data_url,
    text_from_blocks,
    tool_identity,
    tool_json_schema,
)


def _user_content_block(block: Dict[str, Any]) -> Dict[str, Any]:
    """Convert one canonical multimodal block to Chat Completions content."""

    block_type = block.get("type")
    if block_type == "text":
        return {"type": "text", "text": str(block.get("text", ""))}
    if block_type == "image":
        return {
            "type": "image_url",
            "image_url": {"url": str(block.get("image_url") or "")},
        }
    if block_type == "audio":
        media_type = str(block.get("media_type") or "audio/wav")
        _, data = parse_data_url(str(block.get("audio_url") or ""), media_type)
        audio_format = "mp3" if "mp3" in media_type or "mpeg" in media_type else "wav"
        if audio_format == "wav" and "wav" not in media_type:
            raise ValueError(
                "OpenAI Chat Completions input_audio supports only MP3 and WAV. "
                f"Received {media_type!r}."
            )
        return {
            "type": "input_audio",
            "input_audio": {"data": data, "format": audio_format},
        }
    if block_type == "document":
        media_type = str(block.get("media_type") or "application/pdf")
        media_type, data = parse_data_url(
            str(block.get("document_url") or ""),
            media_type,
        )
        extension = "pdf" if "pdf" in media_type else "txt"
        return {
            "type": "file",
            "file": {
                "filename": f"document.{extension}",
                "file_data": f"data:{media_type};base64,{data}",
            },
        }
    raise ValueError(f"Unsupported OpenAI content block type: {block_type!r}")


def _tool_text(message: Dict[str, Any]) -> str:
    """Create the text-only body required by Chat Completions tool messages."""

    texts = [
        str(block.get("text", ""))
        for block in message.get("content", [])
        if block.get("type") == "text"
    ]
    joined = "\n".join(texts).strip()
    if message.get("is_error"):
        return json.dumps({"is_error": True, "error": joined or "Tool execution failed."})
    return joined or json.dumps({"success": True})


def openai_message_formater(
    messages: Sequence[Dict[str, Any]],
    system_prompt: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Convert client history into OpenAI Chat Completions messages.

    Chat Completions allows only text inside a ``role=tool`` message. Rich
    tool-result blocks are therefore sent in a following user message after all
    parallel tool acknowledgements have been emitted.
    """

    formatted: List[Dict[str, Any]] = []
    if system_prompt:
        formatted.append({"role": "system", "content": system_prompt})

    index = 0
    while index < len(messages):
        message = messages[index]
        role = message.get("role")
        content = message.get("content", [])

        if role == "user":
            formatted.append(
                {"role": "user", "content": [_user_content_block(block) for block in content]}
            )
            index += 1
            continue

        if role == "assistant":
            text = "\n".join(
                str(block.get("text", ""))
                for block in content
                if block.get("type") == "text"
            ).strip()
            tool_calls = [
                {
                    "id": block.get("tool_call_id"),
                    "type": "function",
                    "function": {
                        "name": block.get("name"),
                        "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                    },
                }
                for block in content
                if block.get("type") == "tool_call"
            ]
            assistant: Dict[str, Any] = {"role": "assistant", "content": text or None}
            if tool_calls:
                assistant["tool_calls"] = tool_calls
            formatted.append(assistant)
            index += 1
            continue

        if role == "tool":
            rich_parts: List[Dict[str, Any]] = []
            rich_names: List[str] = []
            while index < len(messages) and messages[index].get("role") == "tool":
                tool_message = messages[index]
                content_blocks = tool_message.get("content", [])
                vision_prompts = []
                if tool_message.get("tool_name") == "browser_vision":
                    vision_prompts = [
                        block
                        for block in content_blocks
                        if block.get("type") == "text"
                        and str(block.get("text", "")).startswith(
                            "Analyze this browser screenshot and answer:"
                        )
                    ]
                text_tool_message = {
                    **tool_message,
                    "content": [
                        block for block in content_blocks if block not in vision_prompts
                    ],
                }
                formatted.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_message.get("tool_call_id"),
                        "content": _tool_text(text_tool_message),
                    }
                )
                media_blocks = [
                    block
                    for block in content_blocks
                    if block.get("type") != "text"
                ]
                if media_blocks:
                    rich_names.append(str(tool_message.get("tool_name") or "tool"))
                    rich_parts.extend(
                        _user_content_block(block)
                        for block in [*vision_prompts, *media_blocks]
                    )
                index += 1

            if rich_parts:
                formatted.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "Rich media returned by tool result(s): "
                                    + ", ".join(rich_names)
                                    + "."
                                ),
                            },
                            *rich_parts,
                        ],
                    }
                )
            continue

        raise ValueError(f"Unsupported canonical role for OpenAI: {role!r}")

    return formatted


def openai_tool_formater(tools: Optional[Sequence[Any]]) -> List[Dict[str, Any]]:
    """Convert LangChain structured tools to Chat Completions function tools."""

    formatted: List[Dict[str, Any]] = []
    for tool in tools or []:
        name, description = tool_identity(tool)
        formatted.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": tool_json_schema(tool),
                },
            }
        )
    return formatted


def openai_response_formater(response: Any) -> Dict[str, Any]:
    """Normalize an OpenAI Chat Completion into client history blocks."""

    choices = getattr(response, "choices", None) or []
    if not choices:
        raise ValueError("OpenAI Chat Completions returned no choices.")
    choice = choices[0]
    raw_message = getattr(choice, "message", None)
    message_dict = as_dict(raw_message)

    content_blocks: List[Dict[str, Any]] = []
    content = message_dict.get("content")
    if isinstance(content, str) and content:
        content_blocks.append({"type": "text", "text": content})
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                content_blocks.append({"type": "text", "text": block.get("text", "")})

    for raw_call in message_dict.get("tool_calls") or []:
        function = raw_call.get("function") or {}
        arguments = function.get("arguments") or "{}"
        try:
            parsed_arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
        except json.JSONDecodeError:
            parsed_arguments = {"_raw_arguments": arguments}
        content_blocks.append(
            {
                "type": "tool_call",
                "tool_call_id": raw_call.get("id"),
                "name": function.get("name"),
                "input": parsed_arguments or {},
            }
        )

    usage_raw = as_dict(getattr(response, "usage", None))
    input_tokens = int(usage_raw.get("prompt_tokens") or 0)
    output_tokens = int(usage_raw.get("completion_tokens") or 0)
    usage = {
        "input": input_tokens,
        "output": output_tokens,
        "total_tokens": int(usage_raw.get("total_tokens") or input_tokens + output_tokens),
    }
    canonical_message = {"role": "assistant", "content": content_blocks}
    return {
        "message": canonical_message,
        "tool_calls": [block for block in content_blocks if block.get("type") == "tool_call"],
        "text": text_from_blocks(content_blocks),
        "finish_reason": getattr(choice, "finish_reason", None),
        "model": getattr(response, "model", None),
        "usage": usage,
        "native_message": message_dict,
    }


async def invoke_openai_chat_completions(
    *,
    llm: Any,
    model: str,
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    max_tokens: int,
) -> Any:
    """Invoke OpenAI's native asynchronous Chat Completions API client."""

    client = getattr(llm, "root_async_client", None)
    if client is None and hasattr(llm, "chat"):
        client = llm
    if client is None or not hasattr(client, "chat"):
        raise TypeError("The supplied LLM does not expose an OpenAI async chat client.")

    request: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_completion_tokens": max_tokens,
    }
    if tools:
        request.update(
            {
                "tools": tools,
                "tool_choice": "auto",
                "parallel_tool_calls": True,
            }
        )
    return await client.chat.completions.create(**request)
