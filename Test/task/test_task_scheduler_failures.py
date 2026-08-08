import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.Task.TaskCoordinator.config import TaskCoordinatorSettings
from app.Task.TaskCoordinator.memory import InMemoryTaskCoordinator
from app.Task.task_manager import TaskManager
from app.Task.task_scheduler import TaskScheduler


class TaskSchedulerFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_database_insert_failure_rolls_back_chat_and_runtime(self):
        coordinator = InMemoryTaskCoordinator(
            TaskCoordinatorSettings(
                backend="memory",
                max_running_per_user=1,
                max_running_per_instance=1,
                max_queued_per_instance=2,
            )
        )
        runtime_manager = TaskManager()
        scheduler = TaskScheduler(coordinator, runtime_manager)
        websocket = SimpleNamespace(state=SimpleNamespace())

        async def runner():
            return None

        with (
            patch(
                "app.Task.task_scheduler.create_task",
                new_callable=AsyncMock,
                side_effect=RuntimeError("database unavailable"),
            ),
            patch(
                "app.Task.task_scheduler.send_ws_message",
                new_callable=AsyncMock,
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                await scheduler.submit(
                    task_id="task-1",
                    user_id="user-1",
                    chat_id="chat-1",
                    query="query",
                    websocket=websocket,
                    pool=object(),
                    runner_factory=runner,
                )

        self.assertIsNone(await coordinator.get_task("task-1"))
        self.assertIsNone(runtime_manager.get_state_or_none("task-1"))

        replacement = await coordinator.submit_task(
            task_id="task-2",
            user_id="user-1",
            chat_id="chat-1",
        )
        self.assertTrue(replacement.accepted)
