from pydantic import BaseModel, model_validator, Field
from typing import Literal, Union, List, Optional
from enum import Enum


class SystemInfo(BaseModel) :
    os: str = Field(..., description="Operating system name")
    version: str = Field(..., description="OS version")
    workspace: str = Field(..., description="The workspace path")
    cwd: str = Field(..., description="The current working directory")

class ConsciousFiles(BaseModel):
    aura: Optional[str] = Field(None, description="AURA.md content — rulebook")
    id: Optional[str] = Field(None, description="ID.md content — identity")
    soul: Optional[str] = Field(None, description="SOUL.md content — soul/personality")
    user: Optional[str] = Field(None, description="USER.md content — user knowledge")

class OpenApplications(BaseModel):
    active_apps: list[str] = Field(default_factory=list, description="List of all running applications on screen")
    focused_app: Optional[str] = Field(None, description="Name of the focused application")

class AuraConfig(BaseModel):
    conscious_files: Optional[ConsciousFiles] = None
    open_apps: Optional[OpenApplications] = None
    timezone: str = Field(default="Asia/Kolkata", description="User timezone")
    compression: bool = Field(default=False, description="Enable context compression")
    boot_me: bool = Field(default=False, description="Enable boot process for new agents")
    local_skills: Optional[str] = Field(default=None, description="String containing local skills metadata")

OpenAIModels = Literal['gpt-3.5-turbo', 'gpt-4', 'gpt-4-turbo', 'gpt-4o', 'gpt-4o-mini', 'gpt-4o-mini-high', 'gpt-4.1']
AnthropicModels = Literal['claude-opus-4-7', 'claude-opus-4-6', 'claude-sonnet-4-6']
OpenRouterModels = Literal['kimi-k2', 'deepseek', 'z-ai', 'x-ai', "openai", "xiaomi", "google", "qwen", "nvidia", "upstage"]
GoogleModels = Literal['gemini-2.0-flash', 'gemini-2.5-flash', 'gemini-2.5-flash-lite', 'gemini-2.5-pro', 'gemini-3-pro', 'gemini-3-flash', 'gemini-3-flash-preview']
AgentRouterModels = Literal['claude-opus-4-5-20251101', 'deepseek-r1-0528']

class LLMConfig(BaseModel) :
    provider: Literal['openai', 'anthropic', 'open_router', 'google', 'agent_router']
    model_name: Union[OpenAIModels, AnthropicModels, OpenRouterModels, GoogleModels, AgentRouterModels]
    api_key: Optional[str] = None

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
AGENT_TYPE = Literal["main", "supervisor", "aura", "interaction", "deep_research", "web_scraper", "web_search", "create_file", "delete_file", "edit_file", "insert_str", "rewrite_file", "str_replace", "complete", "ask", "execute_command", "grep", "ls", "ask_user", "glob"]    # type: ignore
RESPONSE_STATUS_TYPE = Literal["success", "failed", "incomplete"]

# Provider mapping for user settings
PROVIDER_MAPPING = {
    "Open AI": "openai",
    "openai": "openai",
    "Anthropic": "anthropic",
    "anthropic": "anthropic",
    "Open Router": "open_router",
    "open_router": "open_router",
    "Gemini": "google",
    "gemini": "google"
}

# Default models for each provider when user overrides
DEFAULT_MODELS = {
    "openai": "gpt-4.1",
    "anthropic": "claude-sonnet-4-6",
    "open_router": "z-ai",
    "google": "gemini-3-flash-preview"
}

# WS_MESSAGE_TYPE is the types of web socket messages between client and the server
#   "client_tool_request",     // Server → Client: Request to run tools on client
#   "client_tool_response",    // Client → Server: Result of client-side tool
#   "server_tool_response",    // Server → Client: Result of server-side tool
#   "error_message",           // Error messages
#   "user_input",               // User's raw input
#   "aura_status",              // Status messages send to aura frontend.
#   "task_request"              // Request coming from the aura frontend for new task.
WS_MESSAGE_TYPE = Literal["client_tool_request", "server_tool_response", "client_tool_response", "error_message", "user_input", "aura_status", "aura_message", "task_request", "aura_thinking", "aura_context_message", "aura_context_tool_response", "aura_context_tool_request"]

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

class Question(BaseModel):
    id: str = Field(..., description="Unique snake_case identifier.")
    question: str = Field(..., description="The question shown to the user.")
    options: List[str] = Field(default=[], description="Optional choices. Max 3. Leave empty [] if only text input is allowed.")
    multi_select: bool = Field(False, description="True = checkboxes (pick many), False = radio (pick one).")
    placeholder: str = Field("", description="Hint text shown in the text input box.")
    required: bool = Field(False, description="if True then user must answer to given question proceed if false user can skip.")

class AskUserToolInput(BaseModel):
    questions: List[Question] = Field(..., description="A list of questions to ask the user. minimum 1 and maximum 5 questions.")

class CreateFileToolInput(BaseModel):
    path: str = Field(..., description="Path to the file to be created, relative to /singulariti_workspace (e.g., 'src/main.py')")
    content: str = Field(..., description="The content to write to the file")
    permissions: str = Field(default="644", description="File permissions in octal string format (default: 644)")
    hide: str = Field(default="false", description="if true then tool call will not be visible to user, using for internal system processing ie, Memory and Conscious Files")

class StrReplaceToolInput(BaseModel):
    path: str = Field(..., description="Path to the target file, relative to /singulariti_workspace (e.g., 'src/main.py')")
    old_str: str = Field(..., description="Text to be replaced (must appear exactly once)")
    new_str: str = Field(..., description="Replacement text")
    hide: str = Field(default="false", description="if true then tool call will not be visible to user, using for internal system processing ie, Memory and Conscious Files")


class RewriteFileToolInput(BaseModel):
    path: str = Field(..., description="Path to the file to be rewritten, relative to /singulariti_workspace (e.g., 'src/main.py')")
    content: str = Field(..., description="The new content to write to the file, replacing all existing content")
    permissions: str = Field(default="644", description="File permissions in octal string format (default: 644)")
    hide: str = Field(default="false", description="if true then tool call will not be visible to user, using for internal system processing, ie, Memory and Conscious Files")
        

class DeleteFileToolInput(BaseModel):
    path: str = Field(..., description="Path to the file to be rewritten, relative to /singulariti_workspace (e.g., 'src/main.py')")
    hide: str = Field(default="false", description="if true then tool call will not be visible to user, using for internal system processing, ie, Memory and Conscious Files")
    
class InsertStrToolInput(BaseModel):
    path: str = Field(..., description="Path to the file to be rewritten, relative to /singulariti_workspace (e.g., 'src/main.py')")
    insert_line_no: int = Field(..., description="number of line where string will be inserted")
    new_str: str = Field(..., description="String to be inserted")
    hide: str = Field(default="false", description="if true then tool call will not be visible to user, using for internal system processing, ie, Memory and Conscious Files")

class EditFileToolInput(BaseModel):
    path: str = Field(..., description="The absolute path to the file you want to edit (e.g., '/home/user/project/src/main.py')")
    instructions: str = Field(..., description="A clear, first-person description of the changes you are making (e.g., 'I am adding a new validation check')")
    code_edit: str = Field(..., description="The precise code changes using // ... existing code ... for unchanged parts")
    hide: str = Field(default="false", description="If 'true', this tool call will not be shown in the UI")
     

class ExecuteCommandToolInput(BaseModel):
    command: str = Field(..., description="The shell command to execute")
    description: str = Field(..., description="Human readable label for approval messages")
    system: Literal["windows", "macos", "linux"] = Field(default="windows", description="users OS to target")
    currentWorkDir: str = Field(..., description="Directory to run the command / Directory where I will run the given command.")
    env: Optional[dict] = Field(None, description="Key-value pairs of environment variables to set for the process")
    yieldMs: Optional[int] = Field(15000, description="Milliseconds to wait before backgrounding the process (default is 15000). If the process finishes within this time, the output is returned directly; otherwise, it returns a sessionId.")
    background: Optional[bool] = Field(False, description="If true, the process is moved to the background immediately without waiting, returning a sessionId.")
    timeout: Optional[int] = Field(300, description="Maximum time in seconds to allow the command to run before it is automatically killed")
    pty: Optional[bool] = Field(False, description="If true, runs the command in a pseudo-terminal (PTY). Required for interactive CLI tools (like vim, nano) or commands that detect TTY")
    security: Literal["low", "high"] = Field(default="low", description="It is the level of the command how secure is it running on the machine.")
    ask: bool = Field(default=True, description="It checks that if the code/command is not so secure to run on the computer then provides true. which we use to ask the permission of the user.")
    hide: str = Field(default="false", description="if true then tool call will not be visible to user, using for internal system processing, ie, Memory and Conscious Files")

class GrepToolInput(BaseModel):
    pattern: str = Field(..., description="regex string to search for (e.g. \"function foo\") within file contents")
    path: Optional[str] = Field(None, description="The file or directory to search in, defaults to currentWorkDir, always be the absolute path")
    currentWorkDir: str = Field(..., description="The current working directory where we are finding the pattern (could be the root directory of thr project), always be the absolute path")
    include: Optional[str] = Field(None, description="Filter files by name or extension using a glob pattern (e.g. \"*.ts\", \"*.{ts,tsx}\", \"*.css\"), If not provided, searches all files.")
    hide: str = Field(default="false", description="if true then tool call will not be visible to user, using for internal system processing, ie, Memory and Conscious Files")


class LSToolInput(BaseModel):
    path: Optional[str] = Field(None, description="The path to list files and directories for. Must be an absolute path. Omit it to use the current workspace directory.")
    ignore: Optional[List[str]] = Field(None, description="Optional: List of global/ignore patterns to skip. eg: ['*.log', 'tmp/*']")
    currentWorkDir: str = Field(..., description="The current working directory or the directory of the project or root, where path is subdirectory(absolute path) inside currentWorkDir, it is an absolute path.")
    hide: str = Field(default="false", description="if true then tool call will not be visible to user, using for internal system processing, ie, Memory and Conscious Files")

class GlobeToolInput(BaseModel):
    pattern: List[str] = Field(..., description="Glob pattern for matching filenames. It is a list of strings and could be one or multiple patterns. e.g.: pattern: ['**/*.ts'] or pattern: ['**/*.test.ts', '**/*.spec.ts', '**/*.test.js']")
    path: str = Field(..., description="Absolute path inside currentWorkDir where the search begins or where the pattern should be searched. Must be the subdirectory of currentWorkDir or same as currentWorkDir.")
    currentWorkDir: str = Field(..., description="Absolute path to the current working directory. All operations must stay inside this directory. Used as the security boundary. consider it is the root of the project.")
    hide: str = Field(default="false", description="if true then tool call will not be visible to user, using for internal system processing, ie, Memory and Conscious Files")
class ReadSkillToolInput(BaseModel):
    skill_name: str = Field(..., description="The name of the skill to read")
    path: str = Field(..., description="The location/path of the skill folder. Use 'default_skill' for default skills.")
    arguments: Optional[dict] = Field(None, description="Optional arguments to pass to the skill if required")
