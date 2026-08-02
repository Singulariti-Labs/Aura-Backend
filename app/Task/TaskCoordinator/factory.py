"""Coordinator construction and the process-wide configured instance."""

from .base import TaskCoordinator
from .config import TaskCoordinatorSettings
from .memory import InMemoryTaskCoordinator


def create_task_coordinator(
    settings: TaskCoordinatorSettings | None = None,
) -> TaskCoordinator:
    """Create the configured coordinator implementation.

    Redis is intentionally not imported by the current release. Adding it later
    requires a ``RedisTaskCoordinator`` implementing the same interface and one
    additional factory branch.
    """

    resolved_settings = settings or TaskCoordinatorSettings.from_env()
    if resolved_settings.backend == "memory":
        return InMemoryTaskCoordinator(resolved_settings)

    if resolved_settings.backend == "redis":
        raise RuntimeError(
            "TASK_COORDINATOR_BACKEND=redis is not available yet. "
            "Install and configure RedisTaskCoordinator before enabling it."
        )

    raise ValueError(
        f"Unsupported task coordinator backend: {resolved_settings.backend!r}"
    )


task_coordinator = create_task_coordinator()
