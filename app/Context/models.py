from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


CompressionStatus = Literal[
    "idle",
    "requested",
    "waiting_for_safe_boundary",
    "compressing",
    "validating",
    "completed",
    "failed",
]


class CompressionConfig(BaseModel):
    enabled: bool = True
    # Compression uses its own tool-free provider call, independent of the
    # model currently running the agent loop.
    compressor_provider: Literal["anthropic"] = "anthropic"
    compressor_model: str = "claude-haiku-4-5-20251001"
    threshold: float = Field(default=0.10, gt=0, lt=1)
    hard_threshold: float = Field(default=0.25, gt=0, le=1)
    target_ratio: float = Field(default=0.40, gt=0, lt=1)
    tail_ratio: float = Field(default=0.20, gt=0, lt=1)
    tail_overflow_multiplier: float = Field(default=1.50, ge=1)
    # Preferred minimum: 3 atomic message blocks. An assistant message and all
    # of its tool results count as one block. Falls back to 2, then 1 when the
    # larger selection cannot fit below the tail soft ceiling.
    min_tail_blocks: int = Field(default=3, ge=1)
    safety_margin_ratio: float = Field(default=0.05, ge=0, lt=0.25)
    compressor_max_output_tokens: int = Field(default=4096, ge=256)

    @model_validator(mode="after")
    def validate_policy(self):
        if self.hard_threshold <= self.threshold:
            raise ValueError("hard_threshold must exceed threshold")
        if self.target_ratio >= self.threshold:
            raise ValueError("target_ratio must be below threshold")
        return self


class RuntimeCheckpoint(BaseModel):
    current_step: Optional[str] = None
    next_action: str = "Continue the task from the preserved recent context."
    completed_tool_call_ids: List[str] = Field(default_factory=list)
    pending_tool_call_ids: List[str] = Field(default_factory=list)
    files_changed: List[str] = Field(default_factory=list)


class CompressorState(BaseModel):
    status: CompressionStatus = "idle"
    compression_id: Optional[str] = None
    trigger_reason: Optional[str] = None
    generation: int = 0
    before_tokens: int = 0
    after_tokens: int = 0
    compression_input_tokens: int = 0
    compression_output_tokens: int = 0
    target_missed: bool = False
    requested_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    last_error: Optional[str] = None


class TokenState(BaseModel):
    context_window: int
    max_output_tokens: int
    current_input_tokens: int = 0
    projected_next_request_tokens: int = 0
    cumulative_input_tokens: int = 0
    cumulative_output_tokens: int = 0


class ContextSnapshot(BaseModel):
    context_id: str
    task_id: str
    chat_id: str
    agent_id: str = "main"
    provider: str
    model: str
    canonical_messages: List[Dict[str, Any]] = Field(default_factory=list)
    compressed_summary: Optional[str] = None
    summarized_start_seq: Optional[int] = None
    summarized_end_seq: Optional[int] = None
    checkpoint: RuntimeCheckpoint = Field(default_factory=RuntimeCheckpoint)
    next_sequence: int = 1
    context_revision: int = 0
    compressor_state: CompressorState = Field(default_factory=CompressorState)
    token_state: TokenState
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CompressionOutcome(BaseModel):
    summary: str
    older_messages: List[Dict[str, Any]]
    preserved_messages: List[Dict[str, Any]]
    summarized_start_seq: Optional[int]
    summarized_end_seq: Optional[int]
    tail_start_seq: Optional[int]
    tail_end_seq: Optional[int]
    preserved_head_seqs: List[int] = Field(default_factory=list)
    before_tokens: int
    after_tokens: int
    summary_tokens: int
    preserved_tail_tokens: int
    compression_input_tokens: int = 0
    compression_output_tokens: int = 0


class CompressionSummary(BaseModel):
    summary: str
    input_tokens: int = 0
    output_tokens: int = 0
