"""Environment-backed configuration for task admission and queueing."""

from dataclasses import dataclass
import os


def _positive_int_from_env(name: str, default: int) -> int:
    """Read a strictly positive integer from the environment."""

    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc

    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True, slots=True)
class TaskCoordinatorSettings:
    """Capacity and backend settings used by the task coordinator."""

    backend: str = "memory"
    max_running_per_user: int = 3
    max_running_per_instance: int = 50
    max_queued_per_instance: int = 500

    @classmethod
    def from_env(cls) -> "TaskCoordinatorSettings":
        """Build validated settings from environment variables."""

        return cls(
            backend=os.getenv("TASK_COORDINATOR_BACKEND", "memory").strip().lower(),
            max_running_per_user=_positive_int_from_env(
                "MAX_RUNNING_TASKS_PER_USER",
                3,
            ),
            max_running_per_instance=_positive_int_from_env(
                "MAX_RUNNING_TASKS_PER_INSTANCE",
                50,
            ),
            max_queued_per_instance=_positive_int_from_env(
                "MAX_QUEUED_TASKS_PER_INSTANCE",
                500,
            ),
        )
