from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from app.Context.Store.base import ContextStore
from app.Context.compressor import ContextCompressor, SummaryCallback
from app.Context.models import (
    CompressionConfig,
    CompressionOutcome,
    ContextSnapshot,
    RuntimeCheckpoint,
    TokenState,
)
from app.Context.token_counter import TokenCounter
from app.LLM.model_token_limits import ModelContextProfile


ClientEventCallback = Callable[[Dict[str, Any]], Awaitable[None]]


def _client_compression_trigger(trigger_reason: Optional[str]) -> str:
    """Return the stable client-facing trigger for a compression event."""

    if trigger_reason in {"runtime_threshold", "provider_context_error"}:
        return "runtime"
    return str(trigger_reason or "manual")


class ContextManager:
    """Own the canonical resumable context for one running agent loop."""

    def __init__(
        self,
        *,
        task_id: str,
        chat_id: str,
        agent_id: str,
        provider: str,
        model: str,
        profile: ModelContextProfile,
        messages: Sequence[Dict[str, Any]],
        store: ContextStore,
        config: Optional[CompressionConfig] = None,
        client_event_callback: Optional[ClientEventCallback] = None,
        compression_id: Optional[str] = None,
    ):
        self.config = config or CompressionConfig()
        self.counter = TokenCounter()
        self.store = store
        self.client_event_callback = client_event_callback
        self._requested_compression_id = compression_id
        self.context_id = f"{task_id}:{agent_id}"
        canonical = copy.deepcopy(list(messages))
        next_sequence = self._normalize_sequences(canonical)
        self.snapshot = ContextSnapshot(
            context_id=self.context_id,
            task_id=task_id,
            chat_id=chat_id,
            agent_id=agent_id,
            provider=provider,
            model=model,
            canonical_messages=canonical,
            next_sequence=next_sequence,
            token_state=TokenState(
                context_window=profile.context_window,
                max_output_tokens=profile.max_output_tokens,
            ),
        )
        self.compressor = ContextCompressor(self.config, self.counter)

    async def initialize(self) -> None:
        await self.store.save(self.context_id, self.snapshot)

    def effective_messages(self) -> List[Dict[str, Any]]:
        messages: List[Dict[str, Any]] = []
        if self.snapshot.compressed_summary:
            messages.append(
                {
                    "role": "user",
                    "synthetic": "compressed_context",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Earlier task context was compressed. Treat this as "
                                "authoritative prior state and do not repeat completed actions.\n\n"
                                f"Summary:\n{self.snapshot.compressed_summary}\n\n"
                                "Runtime checkpoint:\n"
                                + self.snapshot.checkpoint.model_dump_json(indent=2)
                            ),
                        }
                    ],
                }
            )
        messages.extend(copy.deepcopy(self.snapshot.canonical_messages))
        return messages

    def assign_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        assigned = copy.deepcopy(message)
        assigned["sequence"] = self.snapshot.next_sequence
        self.snapshot.next_sequence += 1
        return assigned

    async def record_assistant(self, message: Dict[str, Any]) -> Dict[str, Any]:
        assigned = self.assign_message(message)
        self.snapshot.canonical_messages.append(assigned)
        pending = [
            str(block.get("tool_call_id"))
            for block in assigned.get("content", [])
            if block.get("type") == "tool_call" and block.get("tool_call_id")
        ]
        self.snapshot.checkpoint.pending_tool_call_ids = pending
        assistant_text = "\n".join(
            str(block.get("text") or "")
            for block in assigned.get("content", [])
            if block.get("type") == "text"
        ).strip()
        if assistant_text:
            self.snapshot.checkpoint.current_step = assistant_text[:500]
        self._touch()
        await self.store.save(self.context_id, self.snapshot)
        return assigned

    async def record_tool_batch(
        self,
        results: Sequence[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        assigned = [self.assign_message(result) for result in results]
        self.snapshot.canonical_messages.extend(assigned)
        completed = list(self.snapshot.checkpoint.completed_tool_call_ids)
        latest_calls: Dict[str, Dict[str, Any]] = {}
        for message in reversed(self.snapshot.canonical_messages[:-len(assigned)]):
            if message.get("role") != "assistant":
                continue
            latest_calls = {
                str(block.get("tool_call_id")): block
                for block in message.get("content", [])
                if block.get("type") == "tool_call" and block.get("tool_call_id")
            }
            if latest_calls:
                break
        files_changed = list(self.snapshot.checkpoint.files_changed)
        mutating_tools = {
            "create_file", "edit_file", "rewrite_file", "str_replace",
            "insert_str", "delete_file",
        }
        for result in assigned:
            call_id = result.get("tool_call_id")
            if call_id and call_id not in completed:
                completed.append(str(call_id))
            call = latest_calls.get(str(call_id))
            if call and call.get("name") in mutating_tools and not result.get("is_error"):
                call_input = call.get("input") or {}
                path = call_input.get("path") or call_input.get("filePath")
                if path and str(path) not in files_changed:
                    files_changed.append(str(path))
        self.snapshot.checkpoint.completed_tool_call_ids = completed
        self.snapshot.checkpoint.pending_tool_call_ids = []
        self.snapshot.checkpoint.files_changed = files_changed
        self._touch()
        await self.store.save(self.context_id, self.snapshot)
        return assigned

    def projected_tokens(
        self,
        *,
        system_prompt: str,
        native_tools: Sequence[Dict[str, Any]],
    ) -> int:
        projected = (
            self.counter.count_text(system_prompt)
            + self.counter.count_messages(self.effective_messages())
            + self.counter.count_tools(native_tools)
        )
        self.snapshot.token_state.projected_next_request_tokens = projected
        return projected

    def usable_context_tokens(self) -> int:
        safety = int(
            self.snapshot.token_state.context_window
            * self.config.safety_margin_ratio
        )
        return max(
            1,
            self.snapshot.token_state.context_window
            - self.snapshot.token_state.max_output_tokens
            - safety,
        )

    async def compress_if_needed(
        self,
        *,
        system_prompt: str,
        native_tools: Sequence[Dict[str, Any]],
        summarizer: SummaryCallback,
        force: bool = False,
        reason: str = "runtime_threshold",
        requested_range: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self.config.enabled and not force:
            return None

        before = self.projected_tokens(
            system_prompt=system_prompt,
            native_tools=native_tools,
        )
        usable = self.usable_context_tokens()
        trigger = int(usable * self.config.threshold)
        if not force and before < trigger:
            return None

        state = self.snapshot.compressor_state
        state.compression_id = (
            self._requested_compression_id
            or f"compression_{uuid.uuid4()}"
        )
        self._requested_compression_id = None
        state.status = "compressing"
        state.trigger_reason = reason
        state.requested_at = state.requested_at or datetime.now(timezone.utc)
        state.started_at = datetime.now(timezone.utc)
        state.before_tokens = before
        state.last_error = None
        await self._emit_status("compressing_context", "Optimizing context to continue the task")

        original = self.snapshot.model_copy(deep=True)
        try:
            outcome = await self.compressor.compress(
                self.snapshot,
                summarizer=summarizer,
                before_tokens=before,
                requested_range=requested_range,
            )
            if outcome is None:
                state.status = "completed"
                state.completed_at = datetime.now(timezone.utc)
                await self.store.save(self.context_id, self.snapshot)
                await self._emit_status("processing", "Context is already compact")
                return None

            projected_after = self._candidate_projected_tokens(
                outcome,
                system_prompt=system_prompt,
                native_tools=native_tools,
            )
            if requested_range is None and projected_after >= trigger:
                # One tighter retry: smaller tail and only one preferred block.
                tighter = self.config.model_copy(
                    update={
                        "tail_ratio": max(0.05, self.config.tail_ratio / 2),
                        "min_tail_blocks": 1,
                    }
                )
                retry = await ContextCompressor(tighter, self.counter).compress(
                    self.snapshot,
                    summarizer=summarizer,
                    before_tokens=before,
                )
                if retry is not None:
                    retry_after = self._candidate_projected_tokens(
                        retry,
                        system_prompt=system_prompt,
                        native_tools=native_tools,
                    )
                    if retry_after < projected_after:
                        outcome = retry
                        projected_after = retry_after

            if requested_range is None and projected_after >= trigger:
                raise ValueError(
                    "Compressed context still reaches the compression threshold"
                )

            event = self._apply_outcome(outcome, projected_after)
            await self.store.save(self.context_id, self.snapshot)
            if self.client_event_callback:
                await self.client_event_callback(event)
            await self._emit_status(
                "resuming_task_after_compressing",
                "Context optimized; resuming the task with optimized context",
            )
            return event
        except Exception as exc:
            self.snapshot = original
            self.snapshot.compressor_state.status = "failed"
            self.snapshot.compressor_state.last_error = str(exc)
            await self.store.save(self.context_id, self.snapshot)
            await self._emit_status(
                "runtime_compression_failed",
                "Context optimization failed; original context retained",
            )
            if before >= int(usable * self.config.hard_threshold):
                raise
            return None

    def update_usage(self, usage: Dict[str, Any]) -> None:
        input_tokens = int(usage.get("input") or 0)
        output_tokens = int(usage.get("output") or 0)
        token_state = self.snapshot.token_state
        token_state.current_input_tokens = input_tokens
        token_state.cumulative_input_tokens += input_tokens
        token_state.cumulative_output_tokens += output_tokens

    def _apply_outcome(
        self,
        outcome: CompressionOutcome,
        projected_after: int,
    ) -> Dict[str, Any]:
        previous_start = self.snapshot.summarized_start_seq
        self.snapshot.compressed_summary = outcome.summary
        self.snapshot.summarized_start_seq = (
            previous_start
            if previous_start is not None
            else outcome.summarized_start_seq
        )
        self.snapshot.summarized_end_seq = outcome.summarized_end_seq
        self.snapshot.canonical_messages = copy.deepcopy(outcome.preserved_messages)
        self._touch()

        state = self.snapshot.compressor_state
        state.status = "completed"
        state.generation += 1
        state.after_tokens = projected_after
        state.compression_input_tokens += outcome.compression_input_tokens
        state.compression_output_tokens += outcome.compression_output_tokens
        state.target_missed = projected_after > int(
            self.usable_context_tokens() * self.config.target_ratio
        )
        state.completed_at = datetime.now(timezone.utc)

        timestamp = datetime.now(timezone.utc).isoformat()
        return {
            "type": "compression",
            "schema_version": 1,
            "id": f"cmp_{uuid.uuid4().hex[:8]}",
            "compression_id": state.compression_id,
            "timestamp": timestamp,
            "task_id": self.snapshot.task_id,
            "chat_id": self.snapshot.chat_id,
            "agent_id": self.snapshot.agent_id,
            "context_id": self.context_id,
            "request_id": (
                f"compression_{self.snapshot.task_id}_{state.generation:02d}"
            ),
            "generation": state.generation,
            "context_revision": self.snapshot.context_revision,
            "status": "completed",
            "target_missed": state.target_missed,
            "trigger": _client_compression_trigger(state.trigger_reason),
            "trigger_reason": state.trigger_reason,
            "summary": outcome.summary,
            "checkpoint": self.snapshot.checkpoint.model_dump(),
            "range": {
                "preserved_head_seqs": outcome.preserved_head_seqs,
                "summarized": {
                    "start_seq": self.snapshot.summarized_start_seq,
                    "end_seq": self.snapshot.summarized_end_seq,
                },
                "preserved_tail": {
                    "start_seq": outcome.tail_start_seq,
                    "end_seq": outcome.tail_end_seq,
                },
            },
            "tokens": {
                "before_context": outcome.before_tokens,
                "after_context": projected_after,
                "summary": outcome.summary_tokens,
                "preserved_tail": outcome.preserved_tail_tokens,
            },
            "policy": {
                "compressor_provider": self.config.compressor_provider,
                "compressor_model": self.config.compressor_model,
                "compressor_max_output_tokens": (
                    self.config.compressor_max_output_tokens
                ),
                "threshold": self.config.threshold,
                "hard_threshold": self.config.hard_threshold,
                "target_ratio": self.config.target_ratio,
                "tail_ratio": self.config.tail_ratio,
                "tail_overflow_multiplier": self.config.tail_overflow_multiplier,
                "min_tail_blocks": self.config.min_tail_blocks,
            },
        }

    def terminal_compression_event(
        self,
        *,
        status: str,
        message: str,
        error_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build a terminal event for a compression request with no outcome.

        Successful compression continues to use ``_apply_outcome`` unchanged;
        this shape is only used for already-compact and failed requests.
        """

        state = self.snapshot.compressor_state
        event: Dict[str, Any] = {
            "type": "compression",
            "schema_version": 1,
            "id": f"cmp_{uuid.uuid4().hex[:8]}",
            "compression_id": state.compression_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "task_id": self.snapshot.task_id,
            "chat_id": self.snapshot.chat_id,
            "agent_id": self.snapshot.agent_id,
            "context_id": self.context_id,
            "request_id": (
                f"compression_{self.snapshot.task_id}_{state.generation + 1:02d}"
            ),
            "generation": state.generation,
            "context_revision": self.snapshot.context_revision,
            "status": status,
            "trigger": _client_compression_trigger(state.trigger_reason),
            "trigger_reason": state.trigger_reason,
            "message": message,
        }
        if error_code is not None:
            event["error"] = {
                "code": error_code,
                "message": message,
            }
        return event

    def _candidate_projected_tokens(
        self,
        outcome: CompressionOutcome,
        *,
        system_prompt: str,
        native_tools: Sequence[Dict[str, Any]],
    ) -> int:
        synthetic = {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": outcome.summary + "\n" + self.snapshot.checkpoint.model_dump_json(),
                }
            ],
        }
        return (
            self.counter.count_text(system_prompt)
            + self.counter.count_tools(native_tools)
            + self.counter.count_message(synthetic)
            + self.counter.count_messages(outcome.preserved_messages)
        )

    async def _emit_status(self, status: str, message: str) -> None:
        if self.client_event_callback:
            await self.client_event_callback(
                {
                    "type": "compression_status",
                    "compression_id": self.snapshot.compressor_state.compression_id,
                    "task_id": self.snapshot.task_id,
                    "chat_id": self.snapshot.chat_id,
                    "context_id": self.context_id,
                    "status": status,
                    "message": message,
                }
            )

    def _touch(self) -> None:
        self.snapshot.context_revision += 1
        self.snapshot.updated_at = datetime.now(timezone.utc)

    @staticmethod
    def _normalize_sequences(messages: List[Dict[str, Any]]) -> int:
        previous = 0
        for message in messages:
            value = message.get("sequence")
            if not isinstance(value, int) or value <= previous:
                value = previous + 1
                message["sequence"] = value
            previous = value
        return previous + 1
