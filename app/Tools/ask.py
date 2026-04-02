from typing import Optional
import uuid
import json

from app.Types.agent_types import AskToolInput
from app.Tools.base_tool import BaseTool
from app.LLM.memory import Memory
from app.helper import send_last_assistant_message, save_tool_response, update_memory
from app.api.websocket_utils import send_ws_message
from app.Task.task_manager import task_manager
from app.DB.Queries.agent_event import create_agent_event



class AskTool(BaseTool):
    def __init__(self, task_id: str, chat_id: str, memory: Optional[Memory] = None):
        super().__init__(
            name="ask",
            description="""This tool is used to ask user a question and wait for response. Use for: 1) Requesting clarification on ambiguous requirements, 
                2) Seeking confirmation before proceeding with high-impact changes, 3) Gathering additional information needed to complete a task, 
                4) Offering options and requesting user preference, 5) Validating assumptions when critical to task success. IMPORTANT: Use this tool only when 
                user input is essential to proceed. Always provide clear context and options when applicable. Include relevant attachments when the question 
                relates to specific files or resources.""",
            memory=memory,
            args_schema=AskToolInput
        )
        self.memory = memory
        self.task_id = task_id
        self.chat_id = chat_id

    async def run(self, inputs: AskToolInput):

        # Sending the last assistant message to the client.
        await send_last_assistant_message(memory=self.memory, task_id=self.task_id, chat_id=self.chat_id, tool_name="ask")
        text = inputs.text
        attachments = inputs.attachments
        tool_call_id = str(uuid.uuid4())
        
        # WIP**
        # send ws message to client and close the task here and Compress the messages and feed them to next task run new task with the user_input.

        task_state = task_manager.get_state(self.task_id)
        websocket = task_state.websocket
        dbpool = task_state.dbpool

        result = {"text": text, "attachments": attachments}
        await send_ws_message(
            websocket=websocket,
            type="server_tool_response",
            task_id=self.task_id,
            chat_id=self.chat_id,
            payload={
                "tool": "ask",
                "tool_call_id": tool_call_id,
                "content":{
                    "role": "tool",                    
                    "message":json.dumps(result),
                    "status": "success"
                },
                "coming_from": "ask_tool_func/server"
            }
        )

        # Insert AURA complex agent event in the DB 
        await create_agent_event(
            pool=dbpool,
            task_id=self.task_id,
            role="tool",
            message_type="server_tool_response",
            tool="ask",
            payload= {
                  "content": {
                    "message": json.dumps(result),
                    "status": "success"
                }
            },
            seq = task_state.get_next_seq()
        )

        update_memory(role="tool", name="ask", tool_call_id=tool_call_id, content=json.dumps(result), memory=self.memory)

        response = {
            "success": True,
            "output": result
        }
    
        return response