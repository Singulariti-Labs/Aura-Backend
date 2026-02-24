from typing import Optional, Literal, Dict, Any, List
from langchain_core.language_models.chat_models import BaseChatModel
import json

from app.LLM.memory import Memory
from app.api.websocket_utils import send_ws_message
from app.Task.task_manager import task_manager
from app.helper import update_memory
from app.DB.Queries.agent_event import create_agent_event

class CommandExecutor():
    def __init__(self, llm: BaseChatModel, task_id: str, chat_id: str, memory: Optional[Memory] = None):
        self.llm = llm
        self.task_id = task_id
        self.chat_id = chat_id
        self.memory = memory
        self.task_state = task_manager.get_state(self.task_id)
        self.websocket = self.task_state.websocket
        self.dbpool = self.task_state.dbpool

    async def execute_command(
        self,
        command: str,
        description: str,
        system: Literal["windows", "macos", "linux"],
        currentWorkDir: str,
        env: Optional[Dict[str, str]] = None,
        yieldMs: int = 15000,
        background: bool = False,
        timeout: int = 300,
        pty: bool = False,
        security: Literal["low", "high"] = "low",
        ask: bool = True,
        tool_call_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes a shell command by sending a request to the client via websocket.
        """
        input_params = {
            "command": command,
            "description": description,
            "system": system,
            "currentWorkDir": currentWorkDir,
            "env": env,
            "yieldMs": yieldMs,
            "background": background,
            "timeout": timeout,
            "pty": pty,
            "security": security,
            "ask": ask
        }

        try:
            # 1. Send websocket request to client
            await send_ws_message(
                websocket=self.websocket,
                type="client_tool_request",
                chat_id=self.chat_id,
                task_id=self.task_id,
                payload={
                    "tool": "execute_command",
                    "input": input_params
                }
            )

            # 2. Insert agent event in the DB
            await create_agent_event(
                pool=self.dbpool,
                task_id=self.task_id,
                role="tool",
                message_type="client_tool_request",
                tool="execute_command",
                payload={"input": input_params},
                seq=self.task_state.get_next_seq()
            )

            # 3. Wait for client tool response
            tool_resp = await task_manager.wait_for_input(self.task_id)
            response_type = tool_resp.get("type")

            if response_type == "client_tool_response":
                payload = tool_resp.get("payload", {})
                if payload.get("tool") == "execute_command":
                    result = payload.get("result", {})
                    
                    content = result.get("content", [{"type": "text", "text": result.get("message", "")}])
                    details = result.get("details", {})
                    
                    # Tool output structure for the LLM
                    tool_output = {
                        "content": content,
                        "details": details
                    }

                    final_result = {
                        "success": result.get("success", False),
                        "output": tool_output
                    }

                    # 4. Update memory
                    assistant_message = f"Executing command: {command} in {currentWorkDir}"
                    update_memory(role="assistant", content=assistant_message, memory=self.memory)
                    update_memory(
                        role="tool",
                        name="execute_command",
                        tool_call_id=tool_call_id,
                        content=json.dumps(final_result),
                        memory=self.memory
                    )

                    return final_result

            return {
                "success": False,
                "output": {
                    "content": [{"type": "text", "text": f"Unexpected response type: {response_type}"}],
                    "details": {"status": "failed"}
                }
            }

        except Exception as e:
            return {
                "success": False,
                "output": {
                    "content": [{"type": "text", "text": f"Error executing command: {str(e)}"}],
                    "details": {"status": "failed"}
                }
            }