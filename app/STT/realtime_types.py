from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class RealtimeSTTMode(str, Enum):
    """Supported post-processing modes for realtime speech input."""

    AUDIO_INPUT = "audio_input"
    AUDIO_TRANSCRIBE = "audio_transcribe"


class RealtimeSTTConfig(BaseModel):
    """Client-provided settings used to open a Deepgram realtime stream."""

    mode: RealtimeSTTMode = RealtimeSTTMode.AUDIO_INPUT
    model: str = Field(default="nova-3")
    language: str = Field(default="en")
    encoding: Optional[str] = Field(default=None)
    sample_rate: Optional[int] = Field(default=None)
    channels: int = Field(default=1, ge=1, le=2)
    interim_results: bool = Field(default=True)
    smart_format: bool = Field(default=True)
    punctuate: bool = Field(default=True)
    endpointing: int = Field(default=600, ge=100, le=5000)


class TranscriptResult(BaseModel):
    """Normalized Deepgram transcript payload for downstream routing."""

    text: str
    is_final: bool = False
    speech_final: bool = False
