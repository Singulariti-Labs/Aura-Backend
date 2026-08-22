from typing import TypedDict, Optional, Literal
from datetime import datetime
from decimal import Decimal

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
    status: str   # 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
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
    plan_code: Literal["free", "mini", "pro", "max"]
    plan_updated_at: datetime
    created_at: datetime  # User creation timestamp
    updated_at: Optional[datetime]  # Last update timestamp

# ---------- User Settings ----------
class UserSettings(TypedDict, total=False):
    """
    Represents user-specific settings.
    """
    id: str  # UUID (primary key)
    user_id: str  # Foreign key to User
    user_settings: str  # JSON String
    created_at: datetime
    updated_at: datetime


# ---------- Rate Limit ----------
class RateLimit(TypedDict, total=False):
    """
    Represents the current rate-limit window for a user.

    Required fields:
    - id, user_id, window_start, window_input_tokens, window_output_tokens,
      window_spent_usd, limit_usd, status, updated_at
    """
    id: str  # UUID (primary key)
    user_id: str  # Foreign key to User.id
    plan_code: Literal["free", "mini", "pro", "max"]
    window_start: datetime  # Start time for the active rate-limit window
    window_input_tokens: int  # Input tokens used in the active window
    window_output_tokens: int  # Output tokens used in the active window
    window_spent_usd: Decimal  # USD spent in the active window
    limit_usd: Decimal  # USD limit for the active window
    status: Literal["active", "blocked"]  # Rate-limit status
    block_reason: Optional[Literal["usage_limit"]]
    plan_expires_at: Optional[datetime]
    updated_at: datetime  # Last update timestamp


# ---------- User Token Usage ----------
class UserTokenUsage(TypedDict, total=False):
    """
    Represents cumulative token usage for a user.

    Required fields:
    - id, user_id, total_input_tokens, total_output_tokens, total_spent_usd,
      updated_at
    """
    id: str  # UUID (primary key)
    user_id: str  # Foreign key to User.id
    total_input_tokens: int  # Total input tokens used by the user
    total_output_tokens: int  # Total output tokens used by the user
    total_spent_usd: Decimal  # Total USD spent by the user
    updated_at: datetime  # Last update timestamp


# ---------- Subscription Plan ----------
class SubscriptionPlan(TypedDict, total=False):
    """One locally cached Free/Mini/Pro/Max plan definition."""

    code: Literal["free", "mini", "pro", "max"]
    name: str
    price_cents: int
    currency: str
    billing_interval: Optional[Literal["month", "year"]]
    stripe_product_id: Optional[str]
    stripe_price_id: Optional[str]
    usage_limit_usd: Decimal
    window_hours: int
    features: dict
    active: bool
    created_at: datetime
    updated_at: datetime


# ---------- User Billing ----------
class UserBilling(TypedDict, total=False):
    """Current local payment or promotional entitlement snapshot."""

    user_id: str
    plan_code: Literal["free", "mini", "pro", "max"]
    entitlement_status: Literal["active", "grace", "restricted"]
    entitlement_source: Literal["free", "promo", "payment", "admin"]
    promo_redemption_id: Optional[str]
    stripe_customer_id: Optional[str]
    stripe_subscription_id: Optional[str]
    stripe_subscription_status: Optional[str]
    stripe_product_id: Optional[str]
    stripe_price_id: Optional[str]
    current_period_start: Optional[datetime]
    current_period_end: Optional[datetime]
    access_started_at: datetime
    access_until: Optional[datetime]
    cancel_at_period_end: bool
    canceled_at: Optional[datetime]
    last_stripe_event_id: Optional[str]
    last_stripe_event_created_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


# ---------- Promo Code ----------
class PromoCode(TypedDict, total=False):
    """A hashed promotion definition; the redeemable plaintext is never stored."""

    id: str
    code_hash: str
    code_hint: str
    plan_code: Literal["mini", "pro", "max"]
    active: bool
    valid_from: datetime
    valid_until: Optional[datetime]
    access_duration_days: Optional[int]
    max_redemptions: Optional[int]
    redemption_count: int
    metadata: dict
    created_at: datetime
    updated_at: datetime


# ---------- Promo Redemption ----------
class PromoRedemption(TypedDict, total=False):
    """A user's immutable record of redeeming one promotional code."""

    id: str
    promo_code_id: str
    user_id: str
    granted_plan_code: Literal["mini", "pro", "max"]
    previous_plan_code: Literal["free", "mini", "pro", "max"]
    redeemed_at: datetime
    access_expires_at: Optional[datetime]
    status: Literal["active", "expired", "revoked"]
    revoked_at: Optional[datetime]
