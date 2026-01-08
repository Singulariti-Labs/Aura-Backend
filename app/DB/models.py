from typing import TypedDict, Optional
from datetime import datetime

# ---------- Task ----------
class Task(TypedDict, total=False):
    """
    Represents a Task in the system.

    Required fields:
    - task_id, user_id, chat_id, query, status, started_at

    Optional fields:
    - finished_at: may be None if task is still running
    """
    id: str  # UUID (primary key)
    task_id: str  # Unique identifier for the task
    user_id: str  # User who owns the task
    chat_id: str  # Chat session this task belongs to
    query: str    # Initial query that triggered the task
    status: str   # 'running' | 'completed' | 'failed'
    started_at: datetime  # When the task started
    finished_at: Optional[datetime]  # When the task finished (None if running)
    is_star: bool  # Whether the user star that task
    is_delete: bool  # Whether the user delete that task


# ---------- Agent Event ----------
class AgentEvent(TypedDict, total=False):
    """
    Represents a single event logged by an agent during task execution.

    Required fields:
    - id, task_id, seq, role, payload, created_at

    Optional fields:
    - message_type, tool
    """
    id: str  # UUID (primary key)
    task_id: str  # Foreign key to Task
    seq: int  # Sequence number of the event in the task
    role: str  # 'assistant' | 'user' | 'tool'
    message_type: Optional[str]  # 'task_request' / 'aura_response' / 'server_tool_request' / 'server_tool_response'
    tool: Optional[str]  # Tool name if applicable
    payload: dict  # JSON payload
    created_at: datetime  # When the event was logged


# ---------- User ----------
class User(TypedDict, total=False):
    """
    Represents a registered user.

    Required fields:
    - user_id, email, name, created_at

    Optional fields:
    - updated_at: may be None if user never updated their profile
    - auth0_id: may be None if user never logged in
    - hashed_password: may be None if user never logged in
    """
    id: str  # UUID (primary key)
    email: str  # User email (unique)
    name: str  # User display name
    auth0_id: Optional[str]  # Auth0 user identifier
    hashed_password: Optional[str]  # Hashed password (optional for Auth0 users)
    created_at: datetime  # User creation timestamp
    updated_at: Optional[datetime]  # Last update timestamp
