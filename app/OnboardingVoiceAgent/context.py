"""Request-local state used by the onboarding WebSocket integration."""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional


_session_id: ContextVar[Optional[str]] = ContextVar(
    "onboarding_voice_session_id",
    default=None,
)
_silent_tools: ContextVar[bool] = ContextVar(
    "onboarding_voice_silent_tools",
    default=False,
)


def current_session_id() -> Optional[str]:
    return _session_id.get()


def silent_tools_enabled() -> bool:
    return _silent_tools.get()


@contextmanager
def onboarding_runtime(session_id: str) -> Iterator[None]:
    """Attach the onboarding session and hide all tool lifecycle UI."""

    session_token = _session_id.set(session_id)
    silent_token = _silent_tools.set(True)
    try:
        yield
    finally:
        _silent_tools.reset(silent_token)
        _session_id.reset(session_token)
