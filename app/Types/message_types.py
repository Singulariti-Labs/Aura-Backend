from pydantic import BaseModel, Field
from typing import Literal, List, Optional, Union, Dict, Any

class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str

class ImageBlock(BaseModel):
    type: Literal["image"] = "image"
    media_type: Literal["image/png", "image/jpeg", "image/gif", "image/webp"]
    image_url: str

class AudioBlock(BaseModel):
    type: Literal["audio"] = "audio"
    media_type: Literal["audio/mp3", "audio/mpeg", "audio/wav", "audio/ogg", "audio/flac", "audio/aac", "audio/webm"]
    audio_url: str  # base64 encoded bytes

class DocumentBlock(BaseModel):
    type: Literal["document"] = "document"
    media_type: str
    document_url: str

class ToolCallBlock(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    tool_call_id: str
    name: str
    input: Dict[str, Any]

UserContentBlock = Union[TextBlock, ImageBlock, AudioBlock, DocumentBlock]
AssistantContentBlock = Union[TextBlock, ToolCallBlock]
ToolResultContentBlock = Union[TextBlock, ImageBlock, AudioBlock, DocumentBlock]

class UserMessage(BaseModel):
    role: Literal["user"] = "user"
    content: List[UserContentBlock]
    timestamp: Optional[float] = None

class UsageInfo(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

class AssistantMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: List[AssistantContentBlock]
    tool_name: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    usage: Optional[UsageInfo] = None
    finish_reason: Optional[str] = None
    llm_duration_ms: Optional[int] = None
    timestamp: Optional[float] = None
    is_error: bool = False

class ToolResultMessage(BaseModel):
    role: Literal["tool"] = "tool"
    tool_call_id: str
    tool_name: str
    content: List[ToolResultContentBlock]
    is_error: bool = False
    timestamp: Optional[float] = None

MessageHistoryItem = Union[UserMessage, AssistantMessage, ToolResultMessage]
