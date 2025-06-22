from fastapi import WebSocket

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
        return self.tasks[task_id]

    def pause_task(self, task_id: str):
        self.tasks[task_id].paused.clear()

    def resume_task(self, task_id: str):
        self.tasks[task_id].paused.set()

    # def cancel_task(self, task_id: str):
    #     self.tasks[task_id].cancelled = True

    async def wait_if_paused(self, task_id: str):
        await self.tasks[task_id].paused.wait()

    def provide_input(self, task_id: str, data):
        self.tasks[task_id].input_queue.put_nowait(data)

    async def wait_for_input(self, task_id: str):
        return await self.tasks[task_id].input_queue.get()