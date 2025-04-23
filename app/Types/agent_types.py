from pydantic import BaseModel, model_validator
from typing import Literal,Union
from enum import Enum

class SystemInfo(BaseModel) :
    os: str
    version: str

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

class StepStatus(Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"