"""Shared helpers for native model-provider bridges."""

from __future__ import annotations

import copy
import json
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


DATA_URL_PATTERN = re.compile(
    r"^data:(?P<mime>[^;,]+)(?:;[^,]*)?;base64,(?P<data>.*)$",
    re.DOTALL,
)


def as_dict(value: Any) -> Dict[str, Any]:
    """Convert dictionaries, Pydantic models, and SDK objects to a dictionary."""

    if isinstance(value, dict):
        return copy.deepcopy(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(exclude_none=True)
        return dumped if isinstance(dumped, dict) else {}
    if hasattr(value, "dict"):
        dumped = value.dict()
        return dumped if isinstance(dumped, dict) else {}
    return {}


def normalize_history(messages: Optional[Sequence[Any]]) -> List[Dict[str, Any]]:
    """Validate and copy the client history without mutating the request payload.

    The accepted top-level roles are ``user``, ``assistant``, and ``tool``.
    Content is always normalized to an array, matching the client contract.
    Unknown metadata is retained because some callers attach useful tracing
    information, but provider formatters only consume documented fields.
    """

    normalized: List[Dict[str, Any]] = []
    for index, raw_message in enumerate(messages or []):
        message = as_dict(raw_message)
        if not message:
            raise ValueError(f"History message at index {index} must be an object.")

        role = message.get("role")
        if role not in {"user", "assistant", "tool"}:
            raise ValueError(
                f"History message at index {index} has unsupported role {role!r}."
            )

        content = message.get("content", [])
        if isinstance(content, str):
            # Be tolerant of old persisted history while keeping the new
            # client-to-server contract block based.
            content = [{"type": "text", "text": content}]
        if not isinstance(content, list):
            raise ValueError(
                f"History message at index {index} must contain a content array."
            )

        message["content"] = [
            as_dict(block) if not isinstance(block, str) else {"type": "text", "text": block}
            for block in content
        ]
        normalized.append(message)
    return normalized


def parse_data_url(value: str, fallback_media_type: str) -> Tuple[str, str]:
    """Return ``(media_type, base64_data)`` from a data URL or raw base64 text."""

    match = DATA_URL_PATTERN.match(value or "")
    if match:
        return match.group("mime"), match.group("data")
    return fallback_media_type, value or ""


def ensure_data_url(value: str, media_type: str) -> str:
    """Return an existing URL unchanged or wrap raw base64 as a data URL."""

    if (value or "").startswith(("data:", "http://", "https://", "gs://")):
        return value
    return f"data:{media_type};base64,{value or ''}"


def is_remote_url(value: str) -> bool:
    """Return whether a content URL refers to a remote resource."""

    return (value or "").startswith(("http://", "https://", "gs://"))


def tool_json_schema(tool: Any) -> Dict[str, Any]:
    """Extract a JSON Schema object from a LangChain structured tool."""

    schema_source = getattr(tool, "args_schema", None)
    if schema_source is None:
        return {"type": "object", "properties": {}}
    if isinstance(schema_source, dict):
        schema = copy.deepcopy(schema_source)
    elif hasattr(schema_source, "model_json_schema"):
        schema = schema_source.model_json_schema()
    elif hasattr(schema_source, "schema"):
        schema = schema_source.schema()
    else:
        schema = {"type": "object", "properties": {}}

    # A top-level Pydantic title is not useful to any provider and consumes
    # prompt tokens. Nested titles are retained because they may be meaningful.
    schema.pop("title", None)
    return schema


def tool_identity(tool: Any) -> Tuple[str, str]:
    """Return a provider-safe tool name and its detailed description."""

    name = str(getattr(tool, "name", "") or "").strip()
    if not name:
        raise ValueError("Every tool passed to aura_invoker must have a name.")
    description = str(getattr(tool, "description", "") or "").strip()
    return name, description


def model_name_from_llm(llm: Any) -> str:
    """Read the selected model name from a LangChain or native SDK client."""

    model_name = getattr(llm, "model_name", None) or getattr(llm, "model", None)
    if not model_name:
        raise ValueError("Unable to determine the model name from the LLM instance.")
    return str(model_name)


def configured_output_limit(llm: Any, fallback: int) -> int:
    """Use the backend-configured provider limit before a caller fallback."""

    value = (
        getattr(llm, "max_output_tokens", None)
        or getattr(llm, "max_tokens", None)
        or fallback
    )
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(fallback)


def text_from_blocks(blocks: Iterable[Dict[str, Any]]) -> str:
    """Join canonical text blocks into the text returned by aura_invoker."""

    return "\n".join(
        str(block.get("text", ""))
        for block in blocks
        if block.get("type") == "text" and block.get("text") is not None
    ).strip()


def json_text(value: Any) -> str:
    """Serialize tool data deterministically while supporting arbitrary objects."""

    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def canonical_tool_result(
    *,
    tool_call: Dict[str, Any],
    result: Any = None,
    error: Optional[BaseException] = None,
) -> Dict[str, Any]:
    """Convert a LangChain/native tool result to the client history schema.

    Rich return dictionaries may contain ``image_base64``/``mime_type`` or a
    canonical ``content`` block array. Large binary values are removed from the
    JSON text block after being promoted to their own media block.
    """

    blocks: List[Dict[str, Any]] = []
    is_error = error is not None

    if error is not None:
        blocks.append({"type": "text", "text": f"{type(error).__name__}: {error}"})
    else:
        raw_result = getattr(result, "content", result)
        artifact = getattr(result, "artifact", None)
        if artifact is not None and isinstance(artifact, dict):
            raw_result = {"content": raw_result, **artifact}

        if isinstance(raw_result, str):
            try:
                decoded_result = json.loads(raw_result)
            except json.JSONDecodeError:
                decoded_result = None
            if isinstance(decoded_result, dict):
                raw_result = decoded_result

        if isinstance(raw_result, dict):
            explicit_content = raw_result.get("content")
            if isinstance(explicit_content, list) and all(
                isinstance(item, dict) and item.get("type") for item in explicit_content
            ):
                blocks.extend(copy.deepcopy(explicit_content))

            image_data = raw_result.get("image_base64")
            if image_data:
                media_type = raw_result.get("mime_type") or raw_result.get("mime") or "image/png"
                blocks.append(
                    {
                        "type": "image",
                        "media_type": media_type,
                        "image_url": ensure_data_url(str(image_data), str(media_type)),
                    }
                )

            base64_images = raw_result.get("base64_images")
            if isinstance(base64_images, list):
                for image_data in base64_images:
                    blocks.append(
                        {
                            "type": "image",
                            "media_type": "image/png",
                            "image_url": ensure_data_url(str(image_data), "image/png"),
                        }
                    )

            text_value = {
                key: value
                for key, value in raw_result.items()
                if key not in {"content", "image_base64", "base64_images"}
            }
            if text_value or not blocks:
                blocks.insert(0, {"type": "text", "text": json_text(text_value)})
            is_error = raw_result.get("success") is False
        elif isinstance(raw_result, list) and all(isinstance(item, dict) for item in raw_result):
            blocks.extend(copy.deepcopy(raw_result))
        else:
            blocks.append({"type": "text", "text": json_text(raw_result)})

    return {
        "role": "tool",
        "tool_call_id": tool_call["tool_call_id"],
        "tool_name": tool_call["name"],
        "is_error": is_error,
        "content": blocks,
    }


def build_user_message(
    *,
    query: str,
    attached_files: Optional[Sequence[Dict[str, Any]]] = None,
    attached_images: Optional[Sequence[Dict[str, Any]]] = None,
    base_64_images: Optional[Sequence[str] | str] = None,
    screenshot: Any = None,
    system_info: Optional[str] = None,
    today: Optional[str] = None,
) -> Dict[str, Any]:
    """Build one canonical user message from task text and request attachments."""

    blocks: List[Dict[str, Any]] = []

    screenshot_values: List[Any] = []
    if screenshot:
        screenshot_values = screenshot if isinstance(screenshot, list) else [screenshot]
    for shot in screenshot_values:
        if isinstance(shot, dict):
            data = shot.get("data") or shot.get("content") or shot.get("image_base64")
            media_type = shot.get("mime_type") or shot.get("mime") or "image/png"
        else:
            data = shot
            media_type = "image/png"
        if data:
            blocks.append(
                {
                    "type": "image",
                    "media_type": media_type,
                    "image_url": ensure_data_url(str(data), str(media_type)),
                }
            )
    if screenshot_values:
        blocks.append(
            {
                "type": "text",
                "text": (
                    "This is the current state of the user's screen. Use it as context "
                    "when the request refers to something visible on screen."
                ),
            }
        )

    image_values: Sequence[str]
    if isinstance(base_64_images, str):
        image_values = [base_64_images]
    else:
        image_values = base_64_images or []
    for image in image_values:
        blocks.append(
            {
                "type": "image",
                "media_type": "image/png",
                "image_url": ensure_data_url(str(image), "image/png"),
            }
        )

    for image in attached_images or []:
        data = image.get("content") or image.get("data") or image.get("image_base64")
        if not data:
            continue
        media_type = image.get("mime_type") or image.get("mime") or image.get("type") or "image/png"
        blocks.append(
            {
                "type": "image",
                "media_type": media_type,
                "image_url": ensure_data_url(str(data), str(media_type)),
            }
        )

    text_files: List[str] = []
    for file in attached_files or []:
        content = file.get("content")
        if not content:
            continue
        media_type = str(file.get("type") or file.get("media_type") or "text/plain")
        if "pdf" in media_type.lower():
            blocks.append(
                {
                    "type": "document",
                    "media_type": media_type,
                    "document_url": ensure_data_url(str(content), media_type),
                }
            )
        else:
            text_files.append(
                "\n".join(
                    [
                        f"---- name: {file.get('name', '')}",
                        f"---- path: {file.get('path', '')}",
                        "",
                        str(content),
                    ]
                )
            )
    if text_files:
        blocks.append({"type": "text", "text": "\n\n".join(text_files)})

    query_text = f"query: {query}"
    if system_info:
        query_text += f"\nsystem_info: {system_info}"
    if today:
        query_text += f"\ntoday: {today}"
    blocks.append({"type": "text", "text": query_text})

    return {"role": "user", "content": blocks}
