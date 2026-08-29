from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.Context.message_groups import (
    build_atomic_blocks,
    flatten_blocks,
    sanitize_messages_for_compressor,
    select_preserved_tail,
    reduce_oversized_tail_block,
)
from app.Context.models import (
    CompressionConfig,
    CompressionOutcome,
    CompressionSummary,
    ContextSnapshot,
)
from app.Context.token_counter import TokenCounter


SummaryCallback = Callable[[str], Awaitable[CompressionSummary]]


class ContextCompressor:
    def __init__(self, config: CompressionConfig, counter: TokenCounter):
        self.config = config
        self.counter = counter

    async def compress(
        self,
        snapshot: ContextSnapshot,
        *,
        summarizer: SummaryCallback,
        before_tokens: int,
        requested_range: Optional[Dict[str, Any]] = None,
    ) -> Optional[CompressionOutcome]:
        blocks = build_atomic_blocks(snapshot.canonical_messages, self.counter)
        preserved_head_seqs: List[int] = []
        requested_summary_start: Optional[int] = None
        requested_summary_end: Optional[int] = None
        requested_tail_start: Optional[int] = None
        requested_tail_end: Optional[int] = None
        if requested_range is not None:
            (
                older_blocks,
                tail_blocks,
                preserved_head_seqs,
                requested_summary_start,
                requested_summary_end,
                requested_tail_start,
                requested_tail_end,
            ) = self._select_requested_range(blocks, requested_range)
        else:
            trigger_tokens = int(
                self._usable_context(snapshot) * self.config.threshold
            )
            tail_budget = max(1, int(trigger_tokens * self.config.tail_ratio))
            soft_ceiling = max(
                tail_budget,
                int(tail_budget * self.config.tail_overflow_multiplier),
            )
            older_blocks, tail_blocks = select_preserved_tail(
                blocks,
                tail_budget=tail_budget,
                soft_ceiling=soft_ceiling,
                min_tail_blocks=self.config.min_tail_blocks,
            )
            if (
                len(tail_blocks) == 1
                and tail_blocks[0].token_count > soft_ceiling
            ):
                tail_blocks = [
                    reduce_oversized_tail_block(
                        tail_blocks[0],
                        counter=self.counter,
                        target_tokens=soft_ceiling,
                    )
                ]
        older = flatten_blocks(older_blocks)
        tail = flatten_blocks(tail_blocks)
        if not older:
            return None

        compressor_input = {
            "previous_summary": snapshot.compressed_summary,
            "older_messages": sanitize_messages_for_compressor(older),
        }
        summary_result = await summarizer(
            json.dumps(compressor_input, ensure_ascii=False, default=str)
        )
        summary = summary_result.summary.strip()
        if not summary:
            raise ValueError("Compressor returned an empty summary")

        summary_tokens = self.counter.count_text(summary)
        tail_tokens = self.counter.count_messages(tail)
        checkpoint_tokens = self.counter.count_text(
            snapshot.checkpoint.model_dump_json()
        )
        after_tokens = summary_tokens + tail_tokens + checkpoint_tokens
        return CompressionOutcome(
            summary=summary,
            older_messages=older,
            preserved_messages=tail,
            summarized_start_seq=(
                requested_summary_start
                if requested_range is not None
                else self._first_sequence(older)
            ),
            summarized_end_seq=(
                requested_summary_end
                if requested_range is not None
                else self._last_sequence(older)
            ),
            tail_start_seq=(
                requested_tail_start
                if requested_range is not None
                else self._first_sequence(tail)
            ),
            tail_end_seq=(
                requested_tail_end
                if requested_range is not None
                else self._last_sequence(tail)
            ),
            preserved_head_seqs=preserved_head_seqs,
            before_tokens=before_tokens,
            after_tokens=after_tokens,
            summary_tokens=summary_tokens,
            preserved_tail_tokens=tail_tokens,
            compression_input_tokens=summary_result.input_tokens,
            compression_output_tokens=summary_result.output_tokens,
        )

    @staticmethod
    def _select_requested_range(
        blocks,
        requested_range: Dict[str, Any],
    ):
        summarized = requested_range.get("summarized")
        preserved_tail = requested_range.get("preserved_tail")
        if not isinstance(summarized, dict) or not isinstance(preserved_tail, dict):
            raise ValueError(
                "Compression range requires summarized and preserved_tail objects"
            )

        summary_start = summarized.get("start_seq")
        summary_end = summarized.get("end_seq")
        tail_start = preserved_tail.get("start_seq")
        tail_end = preserved_tail.get("end_seq")
        values = (summary_start, summary_end, tail_start, tail_end)
        if any(not isinstance(value, int) for value in values):
            raise ValueError("Compression range sequence values must be integers")
        if summary_start > summary_end or tail_start > tail_end:
            raise ValueError("Compression range start_seq must not exceed end_seq")
        if summary_end >= tail_start:
            raise ValueError("Summarized and preserved-tail ranges must not overlap")

        raw_head = requested_range.get("preserved_head_seqs", [])
        if not isinstance(raw_head, list) or any(
            not isinstance(value, int) for value in raw_head
        ):
            raise ValueError("preserved_head_seqs must be an array of integers")
        preserved_head_seqs = list(raw_head)
        if any(summary_start <= value <= summary_end for value in preserved_head_seqs):
            raise ValueError("Preserved-head sequences cannot be summarized")

        older_blocks = []
        preserved_blocks = []
        for block in blocks:
            if not block.complete:
                raise ValueError("Cannot compress while a tool-call block is incomplete")
            summarized_flags = []
            for message in block.messages:
                sequence = message.get("sequence")
                if not isinstance(sequence, int):
                    raise ValueError("Every ranged compression message needs a sequence")
                summarized_flags.append(summary_start <= sequence <= summary_end)
            if any(summarized_flags) and not all(summarized_flags):
                raise ValueError(
                    "Compression range cannot split a tool call from its results"
                )
            if all(summarized_flags):
                older_blocks.append(block)
            else:
                preserved_blocks.append(block)

        if not older_blocks:
            raise ValueError("Summarized range does not contain any messages")

        return (
            older_blocks,
            preserved_blocks,
            preserved_head_seqs,
            summary_start,
            summary_end,
            tail_start,
            tail_end,
        )

    def _usable_context(self, snapshot: ContextSnapshot) -> int:
        safety = int(
            snapshot.token_state.context_window * self.config.safety_margin_ratio
        )
        return max(
            1,
            snapshot.token_state.context_window
            - snapshot.token_state.max_output_tokens
            - safety,
        )

    @staticmethod
    def _first_sequence(messages: List[dict]) -> Optional[int]:
        values = [m.get("sequence") for m in messages if isinstance(m.get("sequence"), int)]
        return min(values) if values else None

    @staticmethod
    def _last_sequence(messages: List[dict]) -> Optional[int]:
        values = [m.get("sequence") for m in messages if isinstance(m.get("sequence"), int)]
        return max(values) if values else None
