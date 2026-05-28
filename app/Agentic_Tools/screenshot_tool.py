from typing import Optional, Dict, Any
import json

from app.LLM.memory import Memory
from app.api.websocket_utils import send_ws_message
from app.Task.task_manager import task_manager
from app.helper import update_memory
from app.DB.Queries.agent_event import create_agent_event
from app.Adapters.format_tool_message import create_tool_response

class ScreenshotToolExecutor():
    def __init__(self, llm, task_id: str, chat_id: str, memory: Optional[Memory] = None):
        self.llm = llm
        self.task_id = task_id
        self.chat_id = chat_id
        self.shared_memory = memory
        self.task_state = task_manager.get_state(self.task_id)
        self.websocket = self.task_state.websocket
        self.dbpool = self.task_state.dbpool

    async def capture_screenshot(
        self,
        reason: Optional[str] = None,
        tool_call_id: Optional[str] = None,
        llm_provider: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Captures a screenshot of the user's screen by delegating to the client via WebSockets.
        """
        try:
            # 1. Send websocket request to client to request a screenshot
            await send_ws_message(
                websocket=self.websocket,
                type="client_tool_request",
                chat_id=self.chat_id,
                task_id=self.task_id,
                payload={
                    "tool": "screenshot",
                    "tool_call_id": tool_call_id,
                    "input": {},
                    "coming_from": "screenshot_tool_func/server"
                }
            )

            # 2. Insert AURA agent event in the DB
            await create_agent_event(
                pool=self.dbpool,
                task_id=self.task_id,
                role="tool",
                message_type="client_tool_request",
                tool="screenshot",
                payload={"input": {}},
                seq=self.task_state.get_next_seq()
            )

            # 3. Wait for client tool response
            tool_resp = await task_manager.wait_for_input(self.task_id)
            response_type = tool_resp.get("type")

            if response_type == "client_tool_response":
                payload = tool_resp.get("payload", {})
                if payload.get("tool") == "screenshot":
                    result = payload.get("result", {})
                    
                    base64_image = result.get("image_base64")
                    mime_type = result.get("mime_type") or result.get("mime") or "image/png"
                    resolution = result.get("resolution", {})

                    if not base64_image:
                        return {
                            "success": False,
                            "output": "Failed to capture the screenshot from client."
                        }

                    # Construct ImageAttachment dictionary
                    image_attachment = {
                        "name": f"screenshot.{mime_type.split('/')[-1]}",
                        "path": f"screenshot.{mime_type.split('/')[-1]}",
                        "content": base64_image,
                        "mime": mime_type
                    }

                    # Format for the specific provider using the adapter
                    options = {
                        "provider": llm_provider or "openai", # default to openai if none
                        "text": "Screenshot captured successfully.",
                        "files": [],
                        "images": [image_attachment]
                    }
                    
                    formatted_content = create_tool_response(options)

                    # Update local memory
                    assistant_message = "Capturing a screenshot of the user's screen..."
                    if reason:
                        assistant_message += f" Reason: {reason}"
                    
                    final_result = {
                        "success": True,
                        "output": "Screenshot captured successfully.",
                    }

                    update_memory(role="assistant", content=assistant_message, memory=self.shared_memory)
                    update_memory(
                        role="tool", 
                        name="screenshot", 
                        tool_call_id=tool_call_id, 
                        content=json.dumps(final_result), 
                        memory=self.shared_memory,
                        base64_images=[base64_image]
                    )

                    # Return the structured tool result with media blocks
                    return {
                        "success": True,
                        "result": {
                            "role": "user",
                            "content": [{
                                "type": "tool_result",
                                "tool_use_id": tool_call_id,
                                "content": formatted_content
                            }]
                        }
                    }

            return {
                "success": False,
                "output": f"Unexpected response type: {response_type}"
            }

        except Exception as e:
            return {
                "success": False,
                "output": f"Error executing capture_screenshot: {str(e)}"
            }
