"""Google Gemini generateContent conversion and response parsing."""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional, Sequence

from app.LLM.model_bridge.common import (
    as_dict,
    is_remote_url,
    parse_data_url,
    text_from_blocks,
    tool_identity,
    tool_json_schema,
)


DUMMY_THOUGHT_SIGNATURE = b"context_engineering_is_the_way_to_go"


def _media_part(block: Dict[str, Any]) -> Dict[str, Any]:
    """Convert one canonical media block to a generateContent Part."""

    block_type = block.get("type")
    field_name = {
        "image": "image_url",
        "audio": "audio_url",
        "document": "document_url",
    }.get(block_type)
    if not field_name:
        raise ValueError(f"Unsupported Gemini media block type: {block_type!r}")
    media_type = str(block.get("media_type") or "application/octet-stream")
    value = str(block.get(field_name) or "")
    if is_remote_url(value):
        return {"file_data": {"mime_type": media_type, "file_uri": value}}
    media_type, data = parse_data_url(value, media_type)
    return {"inline_data": {"mime_type": media_type, "data": data}}


def _user_part(block: Dict[str, Any]) -> Dict[str, Any]:
    """Convert one canonical user block to a generateContent Part."""

    if block.get("type") == "text":
        return {"text": str(block.get("text", ""))}
    return _media_part(block)


def _function_response(message: Dict[str, Any]) -> Dict[str, Any]:
    """Convert one canonical tool result to a Gemini functionResponse Part."""

    text_values = [
        str(block.get("text", ""))
        for block in message.get("content", [])
        if block.get("type") == "text"
    ]
    text = "\n".join(text_values).strip()
    try:
        parsed_text = json.loads(text) if text else None
    except json.JSONDecodeError:
        parsed_text = None

    if isinstance(parsed_text, dict):
        response_body: Dict[str, Any] = parsed_text
    else:
        response_body = {"result": parsed_text if parsed_text is not None else text}
    if message.get("is_error"):
        response_body = {"is_error": True, "error": response_body}

    function_response: Dict[str, Any] = {
        "name": message.get("tool_name"),
        "response": response_body,
    }
    if message.get("tool_call_id"):
        function_response["id"] = message.get("tool_call_id")

    media_parts = [
        _media_part(block)
        for block in message.get("content", [])
        if block.get("type") != "text"
    ]
    if media_parts:
        function_response["parts"] = media_parts
    return {"function_response": function_response}


def gemini_message_formater(
    messages: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Convert client history into Gemini generateContent ``contents``.

    Imported client history cannot contain Gemini's opaque thought signatures.
    A documented dummy signature is attached to the first function call in
    each imported model turn. During a live aura_invoker loop, the complete
    native model Content is appended instead, preserving real signatures.
    """

    contents: List[Dict[str, Any]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        role = message.get("role")
        content = message.get("content", [])

        if role == "user":
            contents.append({"role": "user", "parts": [_user_part(block) for block in content]})
            index += 1
            continue

        if role == "assistant":
            parts: List[Dict[str, Any]] = []
            signature_added = False
            for block in content:
                if block.get("type") == "text":
                    parts.append({"text": str(block.get("text", ""))})
                elif block.get("type") == "tool_call":
                    function_call: Dict[str, Any] = {
                        "name": block.get("name"),
                        "args": block.get("input") or {},
                    }
                    if block.get("tool_call_id"):
                        function_call["id"] = block.get("tool_call_id")
                    part: Dict[str, Any] = {"function_call": function_call}
                    if not signature_added:
                        part["thought_signature"] = DUMMY_THOUGHT_SIGNATURE
                        signature_added = True
                    parts.append(part)
                else:
                    raise ValueError(
                        f"Unsupported canonical assistant block for Gemini: {block.get('type')!r}"
                    )
            contents.append({"role": "model", "parts": parts})
            index += 1
            continue

        if role == "tool":
            parts: List[Dict[str, Any]] = []
            vision_parts: List[Dict[str, Any]] = []
            while index < len(messages) and messages[index].get("role") == "tool":
                tool_message = messages[index]
                function_message = tool_message
                if tool_message.get("tool_name") == "browser_vision":
                    content_blocks = tool_message.get("content", [])
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
                    function_message = {
                        **tool_message,
                        "content": [
                            block
                            for block in content_blocks
                            if block not in vision_prompts and block not in vision_media
                        ],
                    }
                    vision_parts.extend(
                        _user_part(block)
                        for block in [*vision_prompts, *vision_media]
                    )

                parts.append(_function_response(function_message))
                index += 1
            contents.append({"role": "user", "parts": parts})
            if vision_parts:
                contents.append({"role": "user", "parts": vision_parts})
            continue

        raise ValueError(f"Unsupported canonical role for Gemini: {role!r}")

    return contents


def gemini_tool_formater(tools: Optional[Sequence[Any]]) -> List[Dict[str, Any]]:
    """Convert LangChain structured tools to Gemini function declarations."""

    declarations: List[Dict[str, Any]] = []
    for tool in tools or []:
        name, description = tool_identity(tool)
        declarations.append(
            {
                "name": name,
                "description": description,
                # The Python SDK's parameters_json_schema accepts the full
                # Pydantic JSON Schema and performs provider conversion.
                "parameters_json_schema": tool_json_schema(tool),
            }
        )
    return [{"function_declarations": declarations}] if declarations else []


def gemini_response_formater(response: Any) -> Dict[str, Any]:
    """Normalize a Gemini GenerateContentResponse into client history blocks."""

    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        raise ValueError("Gemini generateContent returned no candidates.")
    candidate = candidates[0]
    native_content = getattr(candidate, "content", None)
    parts = getattr(native_content, "parts", None) or []

    content_blocks: List[Dict[str, Any]] = []
    native_tool_call_ids: Dict[str, Optional[str]] = {}
    for part in parts:
        text = getattr(part, "text", None)
        if text:
            content_blocks.append({"type": "text", "text": text})
        function_call = getattr(part, "function_call", None)
        if function_call is not None:
            native_id = getattr(function_call, "id", None)
            canonical_id = native_id or f"gemini_call_{uuid.uuid4().hex}"
            canonical_id = str(canonical_id)
            native_tool_call_ids[canonical_id] = str(native_id) if native_id else None
            content_blocks.append(
                {
                    "type": "tool_call",
                    "tool_call_id": canonical_id,
                    "name": getattr(function_call, "name", None),
                    "input": getattr(function_call, "args", None) or {},
                }
            )

    usage_raw = as_dict(getattr(response, "usage_metadata", None))
    input_tokens = int(usage_raw.get("prompt_token_count") or 0)
    output_tokens = int(usage_raw.get("candidates_token_count") or 0)
    usage = {
        "input": input_tokens,
        "output": output_tokens,
        "total_tokens": int(usage_raw.get("total_token_count") or input_tokens + output_tokens),
    }
    finish_reason = getattr(candidate, "finish_reason", None)
    if hasattr(finish_reason, "value"):
        finish_reason = finish_reason.value
    canonical_message = {"role": "assistant", "content": content_blocks}
    return {
        "message": canonical_message,
        "tool_calls": [block for block in content_blocks if block.get("type") == "tool_call"],
        "text": text_from_blocks(content_blocks),
        "finish_reason": finish_reason,
        "model": getattr(response, "model_version", None),
        "usage": usage,
        "native_message": native_content,
        "native_tool_call_ids": native_tool_call_ids,
    }


def gemini_tool_result_formater(
    messages: Sequence[Dict[str, Any]],
    native_tool_call_ids: Optional[Dict[str, Optional[str]]] = None,
) -> List[Dict[str, Any]]:
    """Format live tool results while preserving optional Gemini-native IDs."""

    native_tool_call_ids = native_tool_call_ids or {}
    adjusted: List[Dict[str, Any]] = []
    for message in messages:
        copied = dict(message)
        canonical_id = str(copied.get("tool_call_id") or "")
        native_id = native_tool_call_ids.get(canonical_id, canonical_id)
        copied["tool_call_id"] = native_id
        adjusted.append(copied)
    return gemini_message_formater(adjusted)


async def invoke_gemini_generate_content(
    *,
    llm: Any,
    model: str,
    system_prompt: str,
    contents: List[Any],
    tools: List[Dict[str, Any]],
    max_tokens: int,
) -> Any:
    """Invoke Google's native asynchronous ``models.generate_content`` API."""

    client = getattr(llm, "async_client", None)
    if client is None and hasattr(llm, "aio"):
        client = llm.aio
    if client is None or not hasattr(client, "models"):
        raise TypeError("The supplied LLM does not expose a Google Gen AI async client.")

    config: Dict[str, Any] = {
        "max_output_tokens": max_tokens,
        "automatic_function_calling": {"disable": True},
    }
    if system_prompt:
        config["system_instruction"] = system_prompt
    if tools:
        config["tools"] = tools
        config["tool_config"] = {"function_calling_config": {"mode": "AUTO"}}
    return await client.models.generate_content(
        model=model,
        contents=contents,
        config=config,
    )
