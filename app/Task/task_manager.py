from fastapi import WebSocket
from app.API.websocket_utils import send_ws_message

import asyncio


class TaskControlState:
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.paused = asyncio.Event()
        self.cancelled = False
        self.input_queue = asyncio.Queue()
        self.paused.set()  # initially not paused
    
class TaskManager:
    def __init__(self):
        self.tasks = {}  # task_id -> TaskControlState
    
    def cancel_task(self, task_id: str):
        state = self.tasks[task_id]
        state.cancelled = True
        if state.task:
            state.task.cancel()  # forcefully cancel

    def create_task(self, task_id: str, websocket: WebSocket):
        self.tasks[task_id] = TaskControlState(websocket=websocket)

    def set_task(self, task_id: str, task: asyncio.Task):
        self.tasks[task_id].task = task

    def get_state(self, task_id: str):
        if task_id not in self.tasks:
            raise KeyError(f"Task ID {task_id} not found in TaskManager.")
        return self.tasks[task_id]

    # WIP** - Pause and Resume Task message not need to provide as we are not going to use it
    async def pause_task(self, task_id: str):
        print(f"⏸ Pausing Task {task_id}")
        self.tasks[task_id].paused.clear()
        # await send_ws_message(
        #     websocket=self.tasks[task_id].websocket,
        #     type="status",
        #     status="paused",
        #     message="Task has been paused by the user",
        #     task_id=task_id
        # )

    async def resume_task(self, task_id: str):
        print(f"▶ Resume Running Task")
        self.tasks[task_id].paused.set()
        # await send_ws_message(
        #     websocket=self.tasks[task_id].websocket,
        #     type="status",
        #     status="resumed",
        #     message="Task has resumed by the user",
        #     task_id=task_id
        # )

    # def cancel_task(self, task_id: str):
    #     self.tasks[task_id].cancelled = True

    async def wait_if_paused(self, task_id: str):
        print(f"⏸ Waiting due to pause state for Task {task_id}")
        await self.tasks[task_id].paused.wait()

    def provide_input(self, task_id: str, data):
        self.tasks[task_id].input_queue.put_nowait(data)

    async def wait_for_input(self, task_id: str):
        print(f"🧭 Waiting For The User Input or client tool response")
        return await self.tasks[task_id].input_queue.get()
    
    def remove_task(self, task_id: str):
        if task_id in self.tasks:
            del self.tasks[task_id]

task_manager = TaskManager()