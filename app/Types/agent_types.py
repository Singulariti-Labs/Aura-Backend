from pydantic import BaseModel, model_validator, Field
from typing import Literal, Union, List, Optional
from enum import Enum


class SystemInfo(BaseModel) :
    os: str = Field(..., description="Operating system name")
    version: str = Field(..., description="OS version")

OpenAIModels = Literal['gpt-3.5-turbo', 'gpt-4', 'gpt-4-turbo', 'gpt-4o', 'gpt-4o-mini', 'gpt-4o-mini-high']
AnthropicModels = Literal['claude-3-sonnet-20240229', 'claude-3-haiku-20240307', 'claude-3-opus-20240229']
OpenRouterModels = Literal['kimi-k2', 'deepseek', 'z-ai']

class LLMConfig(BaseModel) :
    provider: Literal['openai', 'anthropic', 'open_router']
    model_name: Union[OpenAIModels, AnthropicModels, OpenRouterModels]

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
AGENT_TYPE = Literal["main", "supervisor", "aura", "interaction", "deep_research", "web_scraper", "web_search", "create_file", "delete_file", "edit_file", "insert_str", "rewrite_file", "str_replace", "complete", "ask"]    # type: ignore
RESPONSE_STATUS_TYPE = Literal["success", "failed", "incomplete"]

# WS_MESSAGE_TYPE is the types of web socket messages between client and the server
#   "client_tool_request",     // Server → Client: Request to run tools on client
#   "client_tool_response",    // Client → Server: Result of client-side tool
#   "server_tool_response",    // Server → Client: Result of server-side tool
#   "error_message",           // Error messages
#   "user_input",               // User's raw input
#   "aura_status",              // Status messages send to aura frontend.
#   "task_request"              // Request coming from the aura frontend for new task.
WS_MESSAGE_TYPE = Literal["client_tool_request", "server_tool_response", "client_tool_response", "error_message", "user_input", "aura_status", "aura_message", "task_request"]

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

class InteractionToolInput(BaseModel):
    query: str
    system_info: Optional[SystemInfo | str] = None

class DeepResearchToolInput(BaseModel):
    query: str

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

class WebSearchInput(BaseModel):
    query: str
    num_results: Optional[int]

class WebScraperInput(BaseModel):
    urls_string: str
    workspace_path: str
    chat_name: str

class CompleteToolInput(BaseModel):
    text: str = Field(
        ...,
        description=(
            "Completion message describing the final status of the task or project. "
            "Should summarize what was accomplished, key deliverables, and any "
            "important notes for the user. Example: "
            "'I have successfully completed all tasks for your project. Here's what was accomplished: "
            "1. Created the web application with modern UI components "
            "2. Implemented user authentication and database integration "
            "3. Deployed the application to production "
            "4. Created comprehensive documentation'."
        )
    )
    attachments: Optional[Union[str, List[str]]] = Field(
        None,
        description=(
            "List of files or URLs that represent final deliverables or supporting materials. "
            "Examples: 'app/src/main.js, docs/README.md, deployment-config.yaml'. "
            "Always use relative paths to the /workspace directory. "
            "Use this field to share source code, configuration files, documentation, "
            "or any other relevant outputs."
        )
    )

class AskToolInput(BaseModel):
    text: str = Field(
        ...,
        description=(
            "Question text to present to user - should be specific and clearly indicate what information you need. "
            "Include: 1) Clear question or request, 2) Context about why the input is needed, "
            "3) Available options if applicable, 4) Impact of different choices, "
            "5) Any relevant constraints or considerations."
        )
    )
    attachments: Optional[Union[str, List[str]]] = Field(
        None,
        description=(
            "(Optional) List of files or URLs to attach to the question. "
            "Include when: 1) Question relates to specific files or configurations, "
            "2) User needs to review content before answering, "
            "3) Options or choices are documented in files, "
            "4) Supporting evidence or context is needed. "
            "Always use relative paths to /workspace directory."
        )
    )

class CreateFileToolInput(BaseModel):
    path: str = Field(..., description="Path to the file to be created, relative to /singulariti_workspace (e.g., 'src/main.py')")
    content: str = Field(..., description="The content to write to the file")
    permissions: str = Field(default="644", description="File permissions in octal string format (default: 644)")

class StrReplaceToolInput(BaseModel):
    path: str = Field(..., description="Path to the target file, relative to /singulariti_workspace (e.g., 'src/main.py')")
    old_str: str = Field(..., description="Text to be replaced (must appear exactly once)")
    new_str: str = Field(..., description="Replacement text")

class RewriteFileToolInput(BaseModel):
    path: str = Field(..., description="Path to the file to be rewritten, relative to /singulariti_workspace (e.g., 'src/main.py')")
    content: str = Field(..., description="The new content to write to the file, replacing all existing content")
    permissions: str = Field(default="644", description="File permissions in octal string format (default: 644)")

class DeleteFileToolInput(BaseModel):
    path: str = Field(..., description="Path to the file to be rewritten, relative to /singulariti_workspace (e.g., 'src/main.py')")

class InsertStrToolInput(BaseModel):
    path: str = Field(..., description="Path to the file to be rewritten, relative to /singulariti_workspace (e.g., 'src/main.py')")
    insert_line_no: int = Field(..., description="number of line where string will be inserted")
    new_str: str = Field(..., description="String to be inserted")

class EditFileToolInput(BaseModel):
    path: str = Field(..., description="Path to the file to be rewritten, relative to /singulariti_workspace (e.g., 'src/main.py')")
    instructions: str = Field(..., description="A single sentence written in the first person describing what you're changing")
    code_edit: str = Field(..., description="Only the precise lines of code to edit. Use // ... existing code ... for unchanged sections")
