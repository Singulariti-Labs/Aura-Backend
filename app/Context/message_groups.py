from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence

from app.Context.token_counter import TokenCounter


@dataclass(frozen=True)
class AtomicMessageBlock:
    messages: List[Dict[str, Any]]
    token_count: int
    complete: bool = True


def _tool_call_ids(message: Dict[str, Any]) -> list[str]:
    return [
        str(block.get("tool_call_id"))
        for block in message.get("content", [])
        if isinstance(block, dict)
        and block.get("type") == "tool_call"
        and block.get("tool_call_id")
    ]


def build_atomic_blocks(
    messages: Sequence[Dict[str, Any]],
    counter: TokenCounter,
) -> List[AtomicMessageBlock]:
    """Group an assistant tool request and all matching results atomically."""

    blocks: List[AtomicMessageBlock] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        call_ids = _tool_call_ids(message) if message.get("role") == "assistant" else []
        if not call_ids:
            copied = [copy.deepcopy(message)]
            blocks.append(AtomicMessageBlock(copied, counter.count_messages(copied)))
            index += 1
            continue

        grouped = [copy.deepcopy(message)]
        expected = set(call_ids)
        found: set[str] = set()
        cursor = index + 1
        while cursor < len(messages) and messages[cursor].get("role") == "tool":
            result = messages[cursor]
            result_id = str(result.get("tool_call_id") or "")
            if result_id in expected:
                grouped.append(copy.deepcopy(result))
                found.add(result_id)
            cursor += 1

        blocks.append(
            AtomicMessageBlock(
                messages=grouped,
                token_count=counter.count_messages(grouped),
                complete=found == expected,
            )
        )
        index = cursor
    return blocks


def select_preserved_tail(
    blocks: Sequence[AtomicMessageBlock],
    *,
    tail_budget: int,
    soft_ceiling: int,
    min_tail_blocks: int,
) -> tuple[List[AtomicMessageBlock], List[AtomicMessageBlock]]:
    """Return ``(older, tail)`` without splitting any atomic block."""

    if not blocks:
        return [], []
    if any(not block.complete for block in blocks):
        raise ValueError("Cannot compress while a tool-call block is incomplete")

    selected_count = 0
    used = 0
    for block in reversed(blocks):
        proposed = used + block.token_count
        if proposed <= tail_budget:
            selected_count += 1
            used = proposed
            continue
        if proposed <= soft_ceiling:
            selected_count += 1
        break

    # Three blocks are preferred, not mandatory. Try 3, then 2, then 1.
    if selected_count < min_tail_blocks:
        upper = min(min_tail_blocks, len(blocks))
        for count in range(upper, 0, -1):
            tokens = sum(block.token_count for block in blocks[-count:])
            if tokens <= soft_ceiling:
                selected_count = count
                break
        if selected_count == 0:
            # The newest atomic block is required to continue safely. The
            # manager will validate the rebuilt context against the hard limit.
            selected_count = 1

    return list(blocks[:-selected_count]), list(blocks[-selected_count:])


def flatten_blocks(blocks: Sequence[AtomicMessageBlock]) -> List[Dict[str, Any]]:
    return [message for block in blocks for message in block.messages]


def sanitize_messages_for_compressor(
    messages: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Remove inline binary data while retaining useful media references."""

    sanitized = copy.deepcopy(list(messages))
    for message in sanitized:
        sequence = message.get("sequence")
        content = message.get("content", [])
        replacement: list[dict[str, Any]] = []
        for block_index, block in enumerate(content):
            if not isinstance(block, dict) or block.get("type") == "text":
                replacement.append(block)
                continue
            block_type = block.get("type")
            if block_type == "tool_call":
                replacement.append(block)
                continue
            reference = (
                block.get("artifact_id")
                or block.get("image_url")
                or block.get("document_url")
                or block.get("audio_url")
                or f"inline-media:{sequence}:{block_index}"
            )
            if isinstance(reference, str) and reference.startswith("data:"):
                reference = f"inline-media:{sequence}:{block_index}"
            replacement.append(
                {
                    "type": "text",
                    "text": (
                        f"[{block_type} omitted from compression input; "
                        f"reference={reference}; media_type={block.get('media_type')}]."
                    ),
                }
            )
        message["content"] = replacement
    return sanitized


def reduce_oversized_tail_block(
    block: AtomicMessageBlock,
    *,
    counter: TokenCounter,
    target_tokens: int,
) -> AtomicMessageBlock:
    """Emergency-reduce huge tool results without breaking their linkage."""

    reduced = copy.deepcopy(block.messages)
    tool_results = [message for message in reduced if message.get("role") == "tool"]
    if not tool_results:
        return block
    fixed_tokens = counter.count_messages(
        message for message in reduced if message.get("role") != "tool"
    )
    per_result_budget = max(
        64,
        (target_tokens - fixed_tokens) // max(1, len(tool_results)),
    )
    for message in tool_results:
        new_content: list[dict[str, Any]] = []
        for index, content_block in enumerate(message.get("content", [])):
            if content_block.get("type") != "text":
                new_content.append(
                    {
                        "type": "text",
                        "text": (
                            f"[{content_block.get('type')} retained in client history; "
                            f"reference=inline-media:{message.get('sequence')}:{index}]"
                        ),
                    }
                )
                continue
            text = str(content_block.get("text") or "")
            if counter.count_text(text) <= per_result_budget:
                new_content.append(content_block)
                continue
            # Four characters per token is a conservative cross-provider cut.
            char_budget = max(256, per_result_budget * 4)
            half = char_budget // 2
            new_content.append(
                {
                    "type": "text",
                    "text": (
                        text[:half]
                        + "\n...[large tool output reduced; full result remains in client history]...\n"
                        + text[-half:]
                    ),
                }
            )
        message["content"] = new_content
        message["output_reduced"] = True
    return AtomicMessageBlock(
        messages=reduced,
        token_count=counter.count_messages(reduced),
        complete=block.complete,
    )
