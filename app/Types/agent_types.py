from pydantic import BaseModel, ConfigDict, model_validator, Field
from typing import (
    Any,
    Dict,
    List,
    Literal,
    Optional,
    Required,
    Tuple,
    TypedDict,
    Union,
)
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
    """Dynamic application metadata reported by the client.

    Individual application objects intentionally have no fixed schema because
    discovery adapters may attach different platform-specific fields.
    """

    active_apps: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="List of application metadata objects currently on screen",
    )
    focused_app: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Metadata object for the focused application, when available",
    )


class MemoryFileContext(BaseModel):
    """Prompt-safe metadata describing one available memory file."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    description: str
    max_size: Optional[int] = Field(default=None, alias="maxSize")
    usage: Optional[str] = None
    aliases: Optional[List[str]] = None


class MemoryContext(BaseModel):
    """Available memory-file metadata grouped by its client-side target."""

    user: List[MemoryFileContext] = Field(default_factory=list)
    memory: List[MemoryFileContext] = Field(default_factory=list)


class AuraConfig(BaseModel):
    conscious_files: Optional[ConsciousFiles] = None
    open_apps: Optional[OpenApplications] = None
    memory_context: Optional[MemoryContext] = None
    timezone: str = Field(default="Asia/Kolkata", description="User timezone")
    compression: bool = Field(default=True, description="Enable context compression")
    boot_me: bool = Field(default=False, description="Enable boot process for new agents")
    local_skills: Optional[str] = Field(default=None, description="String containing local skills metadata")
    cwd: Optional[str] = Field(default=None, description="The current working directory in which user is working on.")


OpenAIModels = Literal['gpt-3.5-turbo', 'gpt-4', 'gpt-4-turbo', 'gpt-4o', 'gpt-4o-mini', 'gpt-4o-mini-high', 'gpt-4.1', 'gpt-5.6-terra','gpt-5.6-luna', 'gpt-5.5', 'gpt-5.4', 'gpt-5.4-mini', 'gpt-5']
AnthropicModels = Literal['claude-opus-4-7', 'claude-opus-4-6', 'claude-opus-4-8', 'claude-sonnet-4-6', 'claude-haiku-4-5-20251001', 'claude-fable-5', 'claude-opus-4-5-20251101', 'claude-sonnet-5', 'claude-sonnet-4-5-20250929']
OpenRouterModels = Literal['kimi-k2', 'deepseek', 'z-ai', 'x-ai', "openai", "xiaomi", "google", "qwen", "nvidia", "upstage"]
GoogleModels = Literal['gemini-2.0-flash', 'gemini-2.5-flash', 'gemini-2.5-flash-lite', 'gemini-2.5-pro', 'gemini-3-pro', 'gemini-3-flash', 'gemini-3-flash-preview', 'gemini-flash-latest', 'gemini-3.1-pro-preview', 'gemini-3.1-flash-lite']
AgentRouterModels = Literal['claude-opus-4-5-20251101', 'deepseek-r1-0528']

# Backend model metadata used to validate the UI's provider/model selection.
MODEL_NAMES_BY_PROVIDER = {
    "openai": set(OpenAIModels.__args__),
    "anthropic": set(AnthropicModels.__args__),
    "open_router": set(OpenRouterModels.__args__),
    "google": set(GoogleModels.__args__),
    "agent_router": set(AgentRouterModels.__args__),
}

CredentialSource = Literal["platform", "custom"]
ReasoningEffort = Literal["low", "medium", "high"]

class LLMConfig(BaseModel):
    """Validated LLM settings used to construct one task's LLM client."""

    provider: Literal['openai', 'anthropic', 'open_router', 'google', 'agent_router']
    model_name: Union[OpenAIModels, AnthropicModels, OpenRouterModels, GoogleModels, AgentRouterModels]
    api_key: Optional[str] = None
    reasoning_effort: Optional[ReasoningEffort] = Field(
        default=None,
        description="Optional reasoning level reserved for future use.",
    )
    credential_source: Optional[CredentialSource] = Field(
        default=None,
        description="Where the API credential is sourced from.",
    )

    @model_validator(mode="after")
    def validate_model_for_provider(self) -> 'LLMConfig':
        allowed_models = MODEL_NAMES_BY_PROVIDER[self.provider]
        if self.model_name not in allowed_models:
            raise ValueError(
                f"Invalid model '{self.model_name}' for provider '{self.provider}'. "
                f"Allowed models: {sorted(allowed_models)}"
            )
        return self

class Role(str, Enum):
    """Message role options"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"

ROLE_TYPE = Literal["system", "user", "assistant", "tool"]  # type: ignore
AGENT_TYPE = Literal["main", "supervisor", "aura", "interaction", "deep_research", "web_scraper", "web_search", "create_file", "delete_file", "edit_file", "insert_str", "rewrite_file", "str_replace", "patch", "complete", "ask", "execute_command", "grep", "ls", "ask_user", "glob", "get_app_context", "read_file", "screenshot", "browser_navigate", "browser_snapshot", "browser_click", "browser_type", "browser_scroll", "browser_back", "browser_press", "browser_get_images", "browser_vision", "browser_console", "create_memory", "memory_update", "read_memory"]    # type: ignore
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
    "gemini": "google",
    "google": "google",
    "Agent Router": "agent_router",
    "agent_router": "agent_router"
}

# Default models for each provider when user overrides
DEFAULT_MODELS = {
    "openai": "gpt-4.1",
    "anthropic": "claude-sonnet-4-6",
    "open_router": "z-ai",
    "google": "gemini-3-flash-preview",
    "agent_router": "claude-opus-4-5-20251101"
}

# WS_MESSAGE_TYPE is the types of web socket messages between client and the server
#   "client_tool_request",     // Server → Client: Request to run tools on client
#   "client_tool_response",    // Client → Server: Result of client-side tool
#   "server_tool_response",    // Server → Client: Result of server-side tool
#   "error_message",           // Error messages
#   "user_input",               // User's raw input
#   "aura_status",              // Status messages send to aura frontend.
#   "task_request"              // Request coming from the aura frontend for new task.
WS_MESSAGE_TYPE = Literal["client_tool_request", "server_tool_response", "client_tool_response", "error_message", "user_input", "aura_status", "aura_message", "task_request", "aura_thinking", "aura_context_message", "aura_context_tool_response", "aura_context_tool_request", "compression", "context_sequence"]

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

class GetAppContextInput(BaseModel):
    name: str = Field(..., description="Name of the application")
    pid: int = Field(..., description="Process ID of the application")
    hwnd: int = Field(..., description="Window handle of the application")
    exe_path: str = Field(..., description="Executable path of the application")

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

class PatchToolInput(BaseModel):
    """Validated input for targeted replacements and V4A bulk patches."""

    mode: Literal["replace", "patch"] = Field(
        default="replace",
        description=(
            "Edit mode. 'replace' requires path, old_string, and new_string. "
            "'patch' requires V4A patch content. Defaults to 'replace'."
        ),
    )
    path: Optional[str] = Field(
        default=None,
        description="Required in replace mode. Path to the file to edit.",
    )
    old_string: Optional[str] = Field(
        default=None,
        description=(
            "Required in replace mode. Text to find; it must be unique unless "
            "replace_all is true."
        ),
    )
    new_string: Optional[str] = Field(
        default=None,
        description=(
            "Required in replace mode. Replacement text, which must differ from "
            "old_string. Use an empty string to delete the match."
        ),
    )
    replace_all: bool = Field(
        default=False,
        description=(
            "Replace every occurrence instead of requiring a unique match. "
            "Applies only in replace mode."
        ),
    )
    patch: Optional[str] = Field(
        default=None,
        description="Required in patch mode. V4A multi-file patch content.",
    )

    @model_validator(mode="after")
    def validate_mode_inputs(self) -> 'PatchToolInput':
        """Require the fields for the selected mode while allowing deletion."""

        if self.mode == "replace":
            missing = [
                field_name
                for field_name in ("path", "old_string", "new_string")
                if getattr(self, field_name) is None
            ]
            if missing:
                raise ValueError(
                    "replace mode requires: " + ", ".join(missing)
                )
            if not self.path:
                raise ValueError("path must not be empty in replace mode")
            if not self.old_string:
                raise ValueError("old_string must not be empty in replace mode")
            if self.old_string == self.new_string:
                raise ValueError("new_string must differ from old_string")
        elif not self.patch:
            raise ValueError("patch mode requires non-empty patch content")

        return self

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

class GlobToolInput(BaseModel):
    pattern: List[str] = Field(..., description="Glob pattern for matching filenames. It is a list of strings and could be one or multiple patterns. e.g.: pattern: ['**/*.ts'] or pattern: ['**/*.test.ts', '**/*.spec.ts', '**/*.test.js']")
    path: str = Field(..., description="Absolute path inside currentWorkDir where the search begins or where the pattern should be searched. Must be the subdirectory of currentWorkDir or same as currentWorkDir.")
    currentWorkDir: str = Field(..., description="Absolute path to the current working directory. All operations must stay inside this directory. Used as the security boundary. consider it is the root of the project.")
    hide: str = Field(default="false", description="if true then tool call will not be visible to user, using for internal system processing, ie, Memory and Conscious Files")

class ReadSkillToolInput(BaseModel):
    skill_name: str = Field(..., description="The name of the skill to read")
    path: str = Field(..., description="The location/path of the skill folder. Use 'default_skill' for default skills.")
    arguments: Optional[dict] = Field(None, description="Optional arguments to pass to the skill if required")

class ReadFileToolInput(BaseModel):
    filePath: str = Field(..., description="Absolute path to the file or directory to read")
    offset: Optional[int] = Field(1, description="1-indexed. For text/docx/xlsx/csv: line number to start from. For pptx: slide number. Defaults to 1.")
    limit: Optional[int] = Field(2000, description="Max lines (or slides for pptx) to read. Defaults to 2000.")

class ScreenshotToolInput(BaseModel):
    reason: Optional[str] = Field(None, description="Optional explanation for why the screenshot is needed")
    hide: str = Field(default="false", description="if true then tool call will not be visible to user")


MemoryTarget = Literal["memory", "user"]
MemoryUpdateAction = Literal["add", "replace", "remove"]


class CreateMemoryToolInput(BaseModel):
    """Complete contents and metadata for a named durable-memory file."""

    # The client contract rejects unknown properties instead of silently
    # ignoring misspelled metadata or fact-list fields.
    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        ...,
        description=(
            "Name of the memory file without the .md suffix. Examples: "
            "preference creates preference.md, aura creates aura.md, "
            "current-project creates current-project.md."
        ),
    )
    target: Literal["user", "memory"] = Field(
        ...,
        description=(
            "\"user\" stores facts about the user, such as identity, preferences, "
            "communication style, and expectations. \"memory\" stores "
            "assistant/project notes, such as environment facts, project "
            "conventions, tool quirks, durable lessons, and active project facts."
        ),
    )
    description: str = Field(
        ...,
        description=(
            "Short description of what this memory file is for. This helps the "
            "agent decide when this memory file should be loaded, updated, or "
            "used in future tasks."
        ),
    )
    aliases: List[str] = Field(
        ...,
        description=(
            "Alternative names or aliases for this memory file. These help "
            "retrieval and tool selection when the user refers to the same "
            "memory by a different name. Example: [\"prefs\", \"preferences\", "
            "\"style\"]."
        ),
    )
    facts: List[str] = Field(
        ...,
        description=(
            "Complete list of memory facts to store in this file. If the file "
            "does not exist, it is created with these facts. If it already "
            "exists, its metadata and facts are completely rewritten with these "
            "values. Each fact should be short, durable, and independently "
            "useful. Do not include temporary task progress, logs, one-off IDs, "
            "or short-lived details."
        ),
    )


class ReadMemoryToolInput(BaseModel):
    """Identify one named durable-memory file to load from the client."""

    # Reject unknown keys so misspelled file selectors never get ignored.
    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        ...,
        description=(
            "Name of the memory file to read, without the .md suffix. For "
            "example, use \"preference\" to read preference.md, \"aura\" to "
            "read aura.md, or \"current-project\" to read current-project.md."
        ),
    )
    target: Literal["user", "memory"] = Field(
        ...,
        description=(
            "\"user\" reads from user-related memory files, such as preferences, "
            "identity, communication style, and expectations. \"memory\" reads "
            "from assistant/project memory files, such as project facts, "
            "environment facts, conventions, tool quirks, and durable lessons."
        ),
    )


class MemoryUpdateOperation(BaseModel):
    """One operation in an atomic client-side memory update batch."""

    action: MemoryUpdateAction = Field(
        ...,
        description="Operation to apply.",
    )
    content: Optional[str] = Field(
        default=None,
        description="Entry content for add or replace. Alias: new_text.",
    )
    new_text: Optional[str] = Field(
        default=None,
        description="Alias for content.",
    )
    old_text: Optional[str] = Field(
        default=None,
        description=(
            "Substring identifying the existing entry for replace or remove."
        ),
    )


class MemoryUpdateToolInput(BaseModel):
    """Input accepted by the client-side durable-memory update tool.

    Conditional action validation is intentionally performed by the client. Its
    structured validation response includes the current memory state and usage,
    which must be returned to the model without being replaced by a local error.
    """

    action: Optional[MemoryUpdateAction] = Field(
        default=None,
        description=(
            "The action to perform in single-operation shape. Omit when using "
            "operations."
        ),
    )
    name: str = Field(
        ...,
        description=(
            "Name of the memory file without the .md suffix. Example: preference "
            "means preference.md."
        ),
    )
    target: MemoryTarget = Field(
        ...,
        description=(
            "\"user\" stores facts about the user, preferences, identity, and "
            "communication style. \"memory\" stores assistant/project notes, "
            "environment facts, project conventions, tool quirks, durable "
            "lessons, and working projects."
        ),
    )
    description: Optional[str] = Field(
        default=None,
        description=(
            "Optional new description for the memory file. Include only if the "
            "file description should be changed."
        ),
    )
    content: Optional[str] = Field(
        default=None,
        description=(
            "The entry content. Required for add and replace in single-operation "
            "shape. Alias: new_text."
        ),
    )
    new_text: Optional[str] = Field(
        default=None,
        description=(
            "Alias for content. If both content and new_text are set, content wins."
        ),
    )
    old_text: Optional[str] = Field(
        default=None,
        description=(
            "Required for replace and remove. A short unique substring identifying "
            "the existing memory entry to modify or remove."
        ),
    )
    operations: Optional[List[MemoryUpdateOperation]] = Field(
        default=None,
        description=(
            "Batch shape. A list of memory update operations applied atomically. "
            "Each operation must include its own memory file name."
        ),
    )


NativeComputerUseAction = Literal[
    "capture",
    "click",
    "double_click",
    "right_click",
    "middle_click",
    "drag",
    "scroll",
    "type",
    "key",
    "set_value",
    "wait",
    "list_apps",
    "list_windows",
    "focus_app",
]

ComputerUseCoordinate = Tuple[int, int]
ComputerUseModifier = Literal[
    "cmd",
    "shift",
    "option",
    "alt",
    "ctrl",
    "fn",
    "win",
    "windows",
    "super",
    "meta",
]


class ComputerUseEscalation(TypedDict):
    """Client recommendation for retrying a failed or unverifiable action."""

    recommended: Literal["px", "foreground", "page"]
    reason: str


class ComputerActionMetadata(TypedDict, total=False):
    """Optional verification metadata returned by input actions."""

    path: str
    verified: bool
    effect: Literal["confirmed", "unverifiable", "suspected_noop"]
    escalation: ComputerUseEscalation
    code: str


class ComputerCaptureTarget(TypedDict, total=False):
    """Native app/window selected for a capture."""

    app: str
    pid: int
    window_id: int
    title: str


class ComputerCaptureImage(TypedDict, total=False):
    """Optional inline screenshot bytes and dimensions returned by the client."""

    mime_type: Required[Literal["image/png", "image/jpeg"]]
    data: str
    width: int
    height: int


class ComputerCaptureBounds(TypedDict):
    """Pixel bounds of a numbered accessibility element."""

    x: int
    y: int
    width: int
    height: int


class ComputerCaptureElement(TypedDict, total=False):
    """One numbered interactable element returned by SOM or AX capture."""

    element: Required[int]
    role: str
    label: str
    value: str
    disabled: bool
    bounds: ComputerCaptureBounds


class ComputerCaptureOutput(TypedDict, total=False):
    """Successful client response for the native ``capture`` action."""

    ok: Required[Literal[True]]
    action: Required[Literal["capture"]]
    mode: Required[Literal["som", "vision", "ax"]]
    target: ComputerCaptureTarget
    screenshot_path: str
    image: ComputerCaptureImage
    elements: List[ComputerCaptureElement]
    total_elements: int
    truncated_elements: int
    degraded: bool
    degraded_reason: str
    summary: str
    raw: Any


class ComputerUseSuccessResult(TypedDict, total=False):
    """Common successful response returned by the desktop client."""

    ok: Required[Literal[True]]
    action: Required[str]
    summary: str
    text: str
    data: Any
    raw: Any
    capture_after: ComputerCaptureOutput
    path: str
    verified: bool
    effect: Literal["confirmed", "unverifiable", "suspected_noop"]
    escalation: ComputerUseEscalation
    code: str


class ComputerUseErrorDetails(TypedDict, total=False):
    """Structured error returned by the desktop client."""

    message: Required[str]
    code: str
    details: Any


class ComputerUseErrorResult(TypedDict, total=False):
    """Common failed response returned by the desktop client."""

    ok: Required[Literal[False]]
    action: Required[str]
    error: Required[ComputerUseErrorDetails]
    effect: Literal["refused", "unverifiable", "suspected_noop"]
    escalation: ComputerUseEscalation
    raw: Any


class ComputerActionOutputBase(TypedDict, total=False):
    """Fields shared by click, drag, scroll, type, key, and set-value outputs."""

    path: str
    verified: bool
    effect: Literal["confirmed", "unverifiable", "suspected_noop", "refused"]
    escalation: ComputerUseEscalation
    summary: str
    capture_after: ComputerCaptureOutput
    error: ComputerUseErrorDetails
    raw: Any


class ComputerClickedTarget(TypedDict, total=False):
    """Element or coordinate targeted by click and double-click actions."""

    element: int
    coordinate: ComputerUseCoordinate
    button: Required[Literal["left", "right", "middle"]]


class ComputerRightClickedTarget(TypedDict, total=False):
    """Element or coordinate targeted by a right-click action."""

    element: int
    coordinate: ComputerUseCoordinate
    button: Required[Literal["right"]]


class ComputerMiddleClickedTarget(TypedDict, total=False):
    """Element or coordinate targeted by a middle-click action."""

    element: int
    coordinate: ComputerUseCoordinate
    button: Required[Literal["middle"]]


class ComputerClickOutput(ComputerActionOutputBase, total=False):
    """Client response for the native ``click`` action."""

    ok: Required[bool]
    action: Required[Literal["click"]]
    clicked: ComputerClickedTarget


class ComputerDoubleClickOutput(ComputerActionOutputBase, total=False):
    """Client response for the native ``double_click`` action."""

    ok: Required[bool]
    action: Required[Literal["double_click"]]
    clicked: ComputerClickedTarget


class ComputerRightClickOutput(ComputerActionOutputBase, total=False):
    """Client response for the native ``right_click`` action."""

    ok: Required[bool]
    action: Required[Literal["right_click"]]
    clicked: ComputerRightClickedTarget


class ComputerMiddleClickOutput(ComputerActionOutputBase, total=False):
    """Client response for the native ``middle_click`` action."""

    ok: Required[bool]
    action: Required[Literal["middle_click"]]
    clicked: ComputerMiddleClickedTarget


class ComputerDraggedTarget(TypedDict, total=False):
    """Source, destination, and optional button reported for a drag."""

    from_element: int
    to_element: int
    from_coordinate: ComputerUseCoordinate
    to_coordinate: ComputerUseCoordinate
    button: Literal["left", "right", "middle"]


class ComputerDragOutput(ComputerActionOutputBase, total=False):
    """Client response for the native ``drag`` action."""

    ok: Required[bool]
    action: Required[Literal["drag"]]
    dragged: ComputerDraggedTarget


class ComputerScrolledTarget(TypedDict, total=False):
    """Target and movement reported for a scroll action."""

    element: int
    coordinate: ComputerUseCoordinate
    direction: Required[Literal["up", "down", "left", "right"]]
    amount: Required[int]


class ComputerScrollOutput(ComputerActionOutputBase, total=False):
    """Client response for the native ``scroll`` action."""

    ok: Required[bool]
    action: Required[Literal["scroll"]]
    scrolled: ComputerScrolledTarget


class ComputerTypedTarget(TypedDict, total=False):
    """Target and character count reported for a type action."""

    element: int
    coordinate: ComputerUseCoordinate
    character_count: Required[int]


class ComputerTypeOutput(ComputerActionOutputBase, total=False):
    """Client response for the native ``type`` action."""

    ok: Required[bool]
    action: Required[Literal["type"]]
    typed: ComputerTypedTarget


class ComputerPressedKeys(TypedDict, total=False):
    """Key combination reported for a key action."""

    keys: Required[str]
    key: str
    modifiers: List[str]


class ComputerKeyOutput(ComputerActionOutputBase, total=False):
    """Client response for the native ``key`` action."""

    ok: Required[bool]
    action: Required[Literal["key"]]
    pressed: ComputerPressedKeys


class ComputerSetValueTarget(TypedDict, total=False):
    """Element and value reported for a set-value action."""

    element: int
    value: Required[str]


class ComputerSetValueOutput(ComputerActionOutputBase, total=False):
    """Client response for the native ``set_value`` action."""

    ok: Required[bool]
    action: Required[Literal["set_value"]]
    set: ComputerSetValueTarget


class ComputerWaitOutput(TypedDict):
    """Successful client response for the native ``wait`` action."""

    ok: Literal[True]
    action: Literal["wait"]
    seconds: float
    summary: str


class ComputerAppInfo(TypedDict, total=False):
    """One application returned by ``list_apps``."""

    name: str
    pid: int
    bundle_id: str
    app_id: str
    path: str
    launch_path: str
    running: bool
    active: bool
    kind: str
    window_count: int
    last_used: Optional[str]


class ComputerListAppsOutput(TypedDict, total=False):
    """Successful client response for the native ``list_apps`` action."""

    ok: Required[Literal[True]]
    action: Required[Literal["list_apps"]]
    apps: Required[List[ComputerAppInfo]]
    summary: str
    raw: Any


class ComputerWindowInfo(TypedDict, total=False):
    """One native window returned by ``list_windows``."""

    window_id: Required[int]
    pid: Required[int]
    app_name: str
    title: str
    bounds: ComputerCaptureBounds
    z_index: Optional[int]
    is_on_screen: bool
    on_current_space: bool
    minimized: bool
    visible: bool


class ComputerListWindowsOutput(TypedDict, total=False):
    """Successful client response for the native ``list_windows`` action."""

    ok: Required[Literal[True]]
    action: Required[Literal["list_windows"]]
    windows: Required[List[ComputerWindowInfo]]
    summary: str
    raw: Any


class ComputerFocusedTarget(TypedDict, total=False):
    """App/window selected by ``focus_app``."""

    app: str
    pid: int
    window_id: int
    raised: Required[bool]


class ComputerFocusAppOutput(TypedDict, total=False):
    """Client response for the native ``focus_app`` action."""

    ok: Required[bool]
    action: Required[Literal["focus_app"]]
    focused: ComputerFocusedTarget
    effect: Literal["confirmed", "unverifiable", "suspected_noop"]
    verified: bool
    summary: str
    error: ComputerUseErrorDetails


ComputerUseResult = Union[
    ComputerCaptureOutput,
    ComputerClickOutput,
    ComputerDoubleClickOutput,
    ComputerRightClickOutput,
    ComputerMiddleClickOutput,
    ComputerDragOutput,
    ComputerScrollOutput,
    ComputerTypeOutput,
    ComputerKeyOutput,
    ComputerSetValueOutput,
    ComputerWaitOutput,
    ComputerListAppsOutput,
    ComputerListWindowsOutput,
    ComputerFocusAppOutput,
    ComputerUseSuccessResult,
    ComputerUseErrorResult,
]


class ComputerUseInput(BaseModel):
    """Input accepted by the client-side native computer-use tool.

    ``action`` is the only globally required field. Other fields remain optional
    in the generated tool schema and are validated according to the selected
    action when a request is created.
    """

    model_config = ConfigDict(extra="forbid")

    action: NativeComputerUseAction = Field(
        ...,
        description="Native desktop action to perform.",
    )
    mode: Optional[Literal["som", "vision", "ax"]] = Field(
        default=None,
        description=(
            "For capture only. 'som' returns a screenshot with numbered "
            "interactable elements and accessibility data; 'vision' returns a "
            "plain screenshot; 'ax' returns accessibility data only. The client "
            "defaults to 'som'."
        ),
    )
    app: Optional[str] = Field(
        default=None,
        description=(
            "App name, executable name, or bundle ID to target. Omit it to use "
            "the frontmost app/window. Use 'screen' for the full screen or "
            "'desktop' for the operating-system desktop/shell."
        ),
    )
    pid: Optional[int] = Field(
        default=None,
        ge=0,
        description="Exact process ID to use when an app name is ambiguous.",
    )
    window_id: Optional[int] = Field(
        default=None,
        ge=0,
        description="Exact native window ID to use when an app has multiple windows.",
    )
    max_elements: Optional[int] = Field(
        default=None,
        ge=1,
        le=1000,
        description=(
            "For capture only. Maximum accessibility elements to return. The "
            "client defaults to 100 and enforces a hard maximum of 1000."
        ),
    )
    element: Optional[int] = Field(
        default=None,
        ge=1,
        description=(
            "One-based element index from the latest SOM capture. Prefer this "
            "over a coordinate for pointer, scroll, and set-value actions."
        ),
    )
    coordinate: Optional[ComputerUseCoordinate] = Field(
        default=None,
        description=(
            "Pixel coordinate [x, y] relative to the captured window. Use only "
            "when no SOM element index is available."
        ),
    )
    button: Optional[Literal["left", "right", "middle"]] = Field(
        default=None,
        description=(
            "Mouse button for a click-like action. The client defaults to left; "
            "right_click and middle_click usually make this unnecessary."
        ),
    )
    modifiers: Optional[List[ComputerUseModifier]] = Field(
        default=None,
        description="Modifier keys to hold during the mouse or keyboard action.",
    )
    from_element: Optional[int] = Field(
        default=None,
        ge=1,
        description="For drag only. Source element index from the latest SOM capture.",
    )
    to_element: Optional[int] = Field(
        default=None,
        ge=1,
        description="For drag only. Destination element index from the latest SOM capture.",
    )
    from_coordinate: Optional[ComputerUseCoordinate] = Field(
        default=None,
        description="For drag only. Source pixel coordinate [x, y].",
    )
    to_coordinate: Optional[ComputerUseCoordinate] = Field(
        default=None,
        description="For drag only. Destination pixel coordinate [x, y].",
    )
    direction: Optional[Literal["up", "down", "left", "right"]] = Field(
        default=None,
        description="For scroll only. Direction in which to scroll.",
    )
    amount: Optional[int] = Field(
        default=None,
        ge=1,
        description="For scroll only. Scroll-wheel ticks; the client defaults to 3.",
    )
    value: Optional[str] = Field(
        default=None,
        description=(
            "For set_value only. Value to assign to the target control. For a "
            "dropdown, use the visible option label."
        ),
    )
    text: Optional[str] = Field(
        default=None,
        description="For type only. Text to type into the focused or targeted control.",
    )
    keys: Optional[str] = Field(
        default=None,
        description=(
            "For key only. A key or '+'-joined key combination, such as 'enter', "
            "'escape', 'ctrl+s', or 'alt+tab'."
        ),
    )
    seconds: Optional[float] = Field(
        default=None,
        ge=0,
        le=30,
        description="For wait only. Seconds to wait, from 0 through 30.",
    )
    raise_window: Optional[bool] = Field(
        default=None,
        description=(
            "For focus_app only. Bring the selected window to the foreground when "
            "true. The client defaults to false."
        ),
    )
    delivery_mode: Optional[Literal["background", "foreground"]] = Field(
        default=None,
        description=(
            "For input actions. Background delivery is the client default and "
            "does not steal focus. Use foreground only when background delivery "
            "fails or foreground interaction is explicitly needed."
        ),
    )
    bring_to_front: Optional[bool] = Field(
        default=None,
        description=(
            "Only valid with delivery_mode='foreground'. Bring the target window "
            "forward before delivering input."
        ),
    )
    capture_after: Optional[bool] = Field(
        default=None,
        description="Return a follow-up capture so the action result can be verified.",
    )

    @model_validator(mode="after")
    def validate_action_input(self) -> "ComputerUseInput":
        """Enforce only the fields that the selected action needs."""

        pointer_actions = {
            "click",
            "double_click",
            "right_click",
            "middle_click",
        }
        if self.action in pointer_actions:
            self._require_one_target("element", "coordinate")

        if self.action == "drag":
            self._require_one_target(
                "from_element",
                "from_coordinate",
                label="drag source",
            )
            self._require_one_target(
                "to_element",
                "to_coordinate",
                label="drag destination",
            )
        elif self.action == "scroll" and self.direction is None:
            raise ValueError("scroll requires direction")
        elif self.action == "type" and self.text is None:
            raise ValueError("type requires text")
        elif self.action == "key" and not self.keys:
            raise ValueError("key requires non-empty keys")
        elif self.action == "set_value":
            self._require_one_target("element", "coordinate")
            if self.value is None:
                raise ValueError("set_value requires value")
        elif self.action == "wait" and self.seconds is None:
            raise ValueError("wait requires seconds")
        elif self.action == "focus_app" and not any(
            target is not None for target in (self.app, self.pid, self.window_id)
        ):
            raise ValueError("focus_app requires app, pid, or window_id")

        if self.bring_to_front is not None and self.delivery_mode != "foreground":
            raise ValueError(
                "bring_to_front is only valid with delivery_mode='foreground'"
            )

        return self

    def _require_one_target(
        self,
        element_field: str,
        coordinate_field: str,
        *,
        label: str = "target",
    ) -> None:
        """Require exactly one element or coordinate for an interaction target."""

        supplied = [
            getattr(self, element_field) is not None,
            getattr(self, coordinate_field) is not None,
        ]
        if sum(supplied) != 1:
            raise ValueError(
                f"{self.action} requires exactly one {label}: "
                f"{element_field} or {coordinate_field}"
            )


class BrowserNavigateToolInput(BaseModel):
    """Input accepted by the client-side browser navigation tool."""

    url: str = Field(
        ...,
        description="The URL to navigate to (e.g., 'https://example.com')",
    )


class BrowserSnapshotToolInput(BaseModel):
    """Input accepted by the client-side browser snapshot tool."""

    full: bool = Field(
        default=False,
        description=(
            "If true, returns complete page content. If false (default), "
            "returns compact view with interactive elements only."
        ),
    )


class BrowserClickToolInput(BaseModel):
    """Input accepted by the client-side browser click tool."""

    ref: str = Field(
        ...,
        description="The element reference from the snapshot (e.g., '@e5', '@e12')",
    )


class BrowserTypeToolInput(BaseModel):
    """Input accepted by the client-side browser type tool."""

    ref: str = Field(
        ...,
        description="The element reference from the snapshot (e.g., '@e3')",
    )
    text: str = Field(..., description="The text to type into the field")


class BrowserScrollToolInput(BaseModel):
    """Input accepted by the client-side browser scroll tool."""

    direction: Literal["up", "down"] = Field(
        ...,
        description="Direction to scroll",
    )


class BrowserBackToolInput(BaseModel):
    """Input for browser history navigation; no arguments are required."""


class BrowserPressToolInput(BaseModel):
    """Input accepted by the client-side browser key press tool."""

    key: str = Field(
        ...,
        description="Key to press (e.g., 'Enter', 'Tab', 'Escape', 'ArrowDown')",
    )


class BrowserGetImagesToolInput(BaseModel):
    """Input for browser image extraction; no arguments are required."""


class BrowserVisionInput(BaseModel):
    """Input accepted by the client-side browser vision tool."""

    question: str = Field(
        ...,
        description=(
            "What you want to know about the page visually. Be specific about "
            "what you're looking for."
        ),
    )
    annotate: bool = Field(
        default=False,
        description=(
            "If true, overlay numbered labels on interactive elements. Useful "
            "for QA , testing and spatial reasoning about page layout."
        ),
    )
    full: bool = Field(
        default=False,
        description=(
            "Capture full page if true, visible viewport only if false."
        ),
    )
    scale_out: Optional[Dict[str, int]] = Field(
        default=None,
        description=(
            "Optional screenshot scaling metadata object containing orig_width, "
            "orig_height, new_width, and new_height integer values."
        ),
    )
    scale_note: Optional[str] = Field(
        default=None,
        description=(
            "Optional note describing the screenshot scaling. When supplied, "
            "the note is included with the visual instruction sent to the model."
        ),
    )



class BrowserConsoleToolInput(BaseModel):
    """Input accepted by the client-side browser console tool."""

    clear: bool = Field(
        default=False,
        description="If true, clear the message buffers after reading",
    )
    expression: Optional[str] = Field(
        default=None,
        description=(
            "JavaScript expression to evaluate in the page context. Runs in the "
            "browser like DevTools console \u2014 full access to DOM, window, document. "
            "Return values are serialized to JSON. Example: 'document.title' or "
            "'document.querySelectorAll(\"a\").length'"
        ),
    )
