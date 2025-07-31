from typing import Optional
import uuid
import json

from app.Tools.base_tool import BaseTool
from app.LLM.memory import Memory

class CompleteTool(BaseTool):
    def __init__(self, task_id: str, memory: Optional[Memory] = None):
        super().__init__(
            name="complete",
            description="""A special tool to indicate you have completed all tasks and are about to enter complete state. Use ONLY when: 
                1) All tasks in todo.md are marked complete [x], 2) The user's original request has been fully addressed, 
                3) There are no pending actions or follow-ups required, 4) You've delivered all final outputs and results to the user. 
                IMPORTANT: This is the ONLY way to properly terminate execution. Never use this tool unless ALL tasks are complete and verified. 
                Always ensure you've provided all necessary outputs and references before using this tool.""",
            memory=memory
        )
        self.memory = memory

    async def run(self) -> str:
        tool_call_id = str(uuid.uuid4())
        response = {
            "status": "completed",
            "message": "Task completed successfully"
        }

        # WIP**
        # send ws message to client.

        try:
            return json.dumps(response, indent=4)
        except Exception as e:
            return f"Error entering complete state: {str(e)}"