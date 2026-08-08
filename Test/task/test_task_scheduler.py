import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.Task.TaskCoordinator.config import TaskCoordinatorSettings
from app.Task.TaskCoordinator.memory import InMemoryTaskCoordinator
from app.Task.TaskCoordinator.models import TaskStatus
from app.Task.task_manager import TaskManager
from app.Task.task_scheduler import TaskScheduler


class _FakeWebSocket:
    def __init__(self):
        self.state = SimpleNamespace()


class TaskSchedulerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        settings = TaskCoordinatorSettings(
            backend="memory",
            max_running_per_user=1,
            max_running_per_instance=1,
            max_queued_per_instance=10,
        )
        self.coordinator = InMemoryTaskCoordinator(settings)
        self.runtime_manager = TaskManager()
        self.scheduler = TaskScheduler(
            self.coordinator,
            self.runtime_manager,
        )
        self.websocket = _FakeWebSocket()
        self.pool = object()

    async def test_terminal_cleanup_promotes_and_starts_next_task(self):
        first_started = asyncio.Event()
        second_started = asyncio.Event()
        second_release = asyncio.Event()

        async def first_runner():
            terminal_status = TaskStatus.COMPLETED
            first_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                terminal_status = TaskStatus.CANCELLED
            finally:
                await self.scheduler.finalize("task-1", terminal_status)

        async def second_runner():
            second_started.set()
            try:
                await second_release.wait()
            finally:
                await self.scheduler.finalize(
                    "task-2",
                    TaskStatus.COMPLETED,
                )

        with (
            patch(
                "app.Task.task_scheduler.create_task",
                new_callable=AsyncMock,
            ),
            patch(
                "app.Task.task_scheduler.update_task_status",
                new_callable=AsyncMock,
                return_value=True,
            ) as update_status,
            patch(
                "app.Task.task_scheduler.send_ws_message",
                new_callable=AsyncMock,
            ),
        ):
            first = await self.scheduler.submit(
                task_id="task-1",
                user_id="user-1",
                chat_id="chat-1",
                query="first",
                websocket=self.websocket,
                pool=self.pool,
                runner_factory=first_runner,
            )
            second = await self.scheduler.submit(
                task_id="task-2",
                user_id="user-1",
                chat_id="chat-2",
                query="second",
                websocket=self.websocket,
                pool=self.pool,
                runner_factory=second_runner,
            )

            self.assertEqual(first.status, TaskStatus.RUNNING)
            self.assertEqual(second.status, TaskStatus.QUEUED)
            await asyncio.wait_for(first_started.wait(), timeout=1)

            self.assertTrue(
                await self.scheduler.cancel("task-1", "user-1")
            )
            await asyncio.wait_for(second_started.wait(), timeout=1)

            self.assertIsNone(
                self.runtime_manager.get_state_or_none("task-1")
            )
            self.assertEqual(
                (await self.coordinator.get_task("task-2")).status,
                TaskStatus.RUNNING,
            )

            second_task = self.runtime_manager.get_state("task-2").task
            second_release.set()
            await asyncio.wait_for(second_task, timeout=1)

            self.assertEqual(await self.coordinator.running_count(), 0)
            self.assertEqual(await self.coordinator.queued_count(), 0)
            self.assertIsNone(
                self.runtime_manager.get_state_or_none("task-2")
            )

            status_values = [
                call.kwargs["status"]
                for call in update_status.await_args_list
            ]
            self.assertIn(TaskStatus.CANCELLED.value, status_values)
            self.assertIn(TaskStatus.RUNNING.value, status_values)
            self.assertIn(TaskStatus.COMPLETED.value, status_values)

    async def test_queued_task_can_be_cancelled_without_starting(self):
        release_running = asyncio.Event()
        queued_started = asyncio.Event()

        async def running_runner():
            try:
                await release_running.wait()
            finally:
                await self.scheduler.finalize(
                    "running",
                    TaskStatus.COMPLETED,
                )

        async def queued_runner():
            queued_started.set()

        with (
            patch(
                "app.Task.task_scheduler.create_task",
                new_callable=AsyncMock,
            ),
            patch(
                "app.Task.task_scheduler.update_task_status",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "app.Task.task_scheduler.send_ws_message",
                new_callable=AsyncMock,
            ),
        ):
            await self.scheduler.submit(
                task_id="running",
                user_id="user-1",
                chat_id="chat-1",
                query="running",
                websocket=self.websocket,
                pool=self.pool,
                runner_factory=running_runner,
            )
            await self.scheduler.submit(
                task_id="queued",
                user_id="user-1",
                chat_id="chat-2",
                query="queued",
                websocket=self.websocket,
                pool=self.pool,
                runner_factory=queued_runner,
            )

            self.assertFalse(
                await self.scheduler.cancel("queued", "another-user")
            )
            self.assertTrue(
                await self.scheduler.cancel("queued", "user-1")
            )
            self.assertFalse(queued_started.is_set())
            self.assertIsNone(await self.coordinator.get_task("queued"))
            self.assertIsNone(
                self.runtime_manager.get_state_or_none("queued")
            )

            running_task = self.runtime_manager.get_state("running").task
            release_running.set()
            await asyncio.wait_for(running_task, timeout=1)
