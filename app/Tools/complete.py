from typing import Optional
import uuid
import json

from app.Tools.base_tool import BaseTool
from app.LLM.memory import Memory
from app.helper import send_last_assistant_message, save_tool_response, update_memory
from app.Task.task_manager import task_manager
from app.Types.agent_types import CompleteToolInput
from app.API.websocket_utils import send_ws_message


class CompleteTool(BaseTool):
    def __init__(self, task_id: str, chat_id: str, memory: Optional[Memory] = None):
        super().__init__(
            name="complete",
            description="""A special tool to indicate you have completed all tasks and are about to enter complete state. Use ONLY when: 
                1) All tasks in todo.md are marked complete [x], 2) The user's original request has been fully addressed, 
                3) There are no pending actions or follow-ups required, 4) You've delivered all final outputs and results to the user. 
                IMPORTANT: This is the ONLY way to properly terminate execution. Never use this tool unless ALL tasks are complete and verified. 
                Always ensure you've provided all necessary outputs and references before using this tool.""",
            memory=memory,
            args_schema=CompleteToolInput
        )
        self.memory = memory
        self.task_id = task_id
        self.chat_id = chat_id

    async def run(self, inputs: CompleteToolInput):
        try:
            # Sending the last assistant message to the client.
            await send_last_assistant_message(memory=self.memory, task_id=self.task_id, chat_id=self.chat_id, tool_name="complete")
            text = inputs.text
            attachments = inputs.attachments
            tool_call_id = str(uuid.uuid4())
            # response = {
            #     "status": "completed",
            #     "message": "Task completed successfully"
            # }

            # WIP**
            # send ws message to client.

            task_state = task_manager.get_state(self.task_id)
            websocket = task_state.websocket

            result = {"text": text, "attachments": attachments}
            await send_ws_message(
                websocket=websocket,
                type="server_tool_response",
                task_id=self.task_id,
                chat_id=self.chat_id,
                payload={
                    "tool": "complete",
                    "content":{
                        "role": "tool",
                        "message":json.dumps(result),
                        "status": "success"
                    }
                }
            )

            update_memory(role="tool", name="complete", tool_call_id=tool_call_id, content=json.dumps(result), memory=self.memory)

            response = {"success": True, "output": result}

            save_tool_response(
                task_id=self.task_id,
                tool_name="complete",
                response= response
            )

            return response
        except Exception as e:
            return {"success": False, "output":f"Error entering complete state: {str(e)}"}
        

      