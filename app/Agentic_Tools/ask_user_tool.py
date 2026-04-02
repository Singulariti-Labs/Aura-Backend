from typing import Optional, Dict, Any, List
import json

from app.LLM.memory import Memory
from app.api.websocket_utils import send_ws_message
from app.Task.task_manager import task_manager
from app.helper import update_memory
from app.DB.Queries.agent_event import create_agent_event
from app.Types.agent_types import Question  # your Pydantic model


class AskUser():
    def __init__(self, llm, task_id: str, chat_id: str, memory: Optional[Memory] = None):
        self.llm = llm
        self.task_id = task_id
        self.chat_id = chat_id
        self.memory = memory
        self.task_state = task_manager.get_state(self.task_id)
        self.websocket = self.task_state.websocket
        self.dbpool = self.task_state.dbpool

    async def ask_user(
        self,
        questions: List[Question],
        tool_call_id: Optional[str] = None
    ) -> Dict[str, Any]:

        # 1. Build JSON payload
        input_params = {
            "questions": [q.model_dump(exclude_none=True) for q in questions]
        }

        try:
            # 2. Send websocket request to client
            await send_ws_message(
                websocket=self.websocket,
                type="client_tool_request",
                chat_id=self.chat_id,
                task_id=self.task_id,
                payload={
                    "tool": "ask_user",
                    "tool_call_id": tool_call_id,
                    "input": input_params,
                    "coming_from": "ask_user_tool_func/server"
                }
            )

            # 3. Insert agent event in DB
            await create_agent_event(
                pool=self.dbpool,
                task_id=self.task_id,
                role="tool",
                message_type="client_tool_request",
                tool="ask_user",
                payload={"input": input_params},
                seq=self.task_state.get_next_seq()
            )

            # 4. Wait for client tool response
            tool_resp = await task_manager.wait_for_input(self.task_id)
            response_type = tool_resp.get("type")

            if response_type == "client_tool_response":
                payload = tool_resp.get("payload", {})
                if payload.get("tool") == "ask_user":
                    result = payload.get("result", {})

                    if not result.get("success", False):
                        return {
                            "success": False,
                            "output": result.get("message", "Error from client")
                        }

                    # 5. answers is { id: value } — no parsing needed
                    answers: Dict[str, Any] = result.get("answers", {})

                    # 6. Check required questions were not skipped
                    for q in questions:
                        if q.required and (q.id not in answers or answers[q.id] is None):
                            return {
                                "success": False,
                                "output": f"Required question '{q.id}' was skipped. Stop the task and explain why you stopped."
                            }

                    final_result = {
                        "success": True,
                        "output": answers  # e.g. {"project_name": "my-app", "features": ["Auth"]}
                    }

                    # 7. Update memory
                    asked = ", ".join([q.question for q in questions])
                    update_memory(role="assistant", content=f"Asked user: {asked}", memory=self.memory)
                    update_memory(
                        role="tool",
                        name="ask_user",
                        tool_call_id=tool_call_id,
                        content=json.dumps(final_result),
                        memory=self.memory
                    )

                    return final_result

            return {
                "success": False,
                "output": f"Unexpected response type: {response_type}"
            }

        except Exception as e:
            return {
                "success": False,
                "output": f"Error executing ask_user: {str(e)}"
            }