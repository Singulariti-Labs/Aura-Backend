from pydantic import BaseModel, model_validator, Field
from typing import Literal, Union, List, Optional
from enum import Enum


class SystemInfo(BaseModel) :
    os: str = Field(..., description="Operating system name")
    version: str = Field(..., description="OS version")

OpenAIModels = Literal['gpt-3.5-turbo', 'gpt-4', 'gpt-4-turbo', 'gpt-4o', 'gpt-4o-mini', 'gpt-4o-mini-high']
AnthropicModels = Literal['claude-3-sonnet-20240229', 'claude-3-haiku-20240307', 'claude-3-opus-20240229']

class LLMConfig(BaseModel) :
    provider: Literal['openai', 'anthropic']
    model_name: Union[OpenAIModels, AnthropicModels]

    @model_validator(mode="after")
    def validate_model_for_provider(self) -> 'LLMConfig':
        if self.provider == "openai" and self.model_name not in OpenAIModels.__args__:
            raise ValueError(f"Invalid model '{self.model_name}' for provider '{self.provider}'. Allowed models: {OpenAIModels.__args__}")
        if self.provider == "anthropic" and self.model_name not in AnthropicModels.__args__:
            raise ValueError(f"Invalid model '{self.model_name}' for provider '{self.provider}'. Allowed models: {AnthropicModels.__args__}")
        return self

class Role(str, Enum):
    """Message role options"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"

ROLE_TYPE = Literal["system", "user", "assistant", "tool"]  # type: ignore
AGENT_TYPE = Literal["main", "supervisor", "interaction"]    # type: ignore
RESPONSE_STATUS_TYPE = Literal["success", "failed", "incomplete"]

class StepStatus(Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"

class Step(BaseModel):
    id: str
    description: str
    thought: str
    dependency: List[str] = Field(default_factory=list) # dependencies can be a list of step ids
    expected_output: str


class StepsList(BaseModel):
    steps: List[Step]

class SupervisorToolInput(BaseModel):
    query: str
    system_info: Optional[SystemInfo | str] = None
    screenshot: Optional[str] = None

class InteractionToolInput(BaseModel):
    query: str
    system_info: Optional[SystemInfo | str] = None
    base64_image: str

class DeepResearchToolInput(BaseModel):
    query: str
    base64_image: Optional[str] = None

class DeepSearchInputQueries(BaseModel):
    query: str
    results: Optional[List[dict]] = []
    reason: str

class DeepResearchActionInput(BaseModel):
    queries: list[DeepSearchInputQueries]
    search_memory: Optional[list[DeepSearchInputQueries]] = []

class GapDetectionToolInput(BaseModel):
    search_memory: Optional[list[DeepSearchInputQueries]] = []
    user_query: str
    summarize_result: str