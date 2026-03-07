from typing import Optional, Dict, Any, List, Literal
from langchain_core.language_models.chat_models import BaseChatModel
import json
import xml.etree.ElementTree as ET

from app.LLM.memory import Memory
from app.api.websocket_utils import send_ws_message
from app.Task.task_manager import task_manager
from app.helper import update_memory
from app.DB.Queries.agent_event import create_agent_event

class AskUser():
    def __init__(self, llm: BaseChatModel, task_id: str, chat_id: str, memory: Optional[Memory] = None):
        self.llm = llm
        self.task_id = task_id
        self.chat_id = chat_id
        self.memory = memory
        self.task_state = task_manager.get_state(self.task_id)
        self.websocket = self.task_state.websocket
        self.dbpool = self.task_state.dbpool

    async def ask_user(
        self,
        question: str,
        type: Literal["input", "single", "multi", "input_with_options"],
        options: Optional[List[str]] = None,
        placeholder: Optional[str] = None,
        required: bool = True,
        tool_call_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Ask the user a question or for their thought during execution.
        """
        
        # 1. Format the XML block
        xml_input = f"<ask_user>\n  <question>{question}</question>\n  <type>{type}</type>\n"
        if options and type != "input":
            xml_input += "  <options>\n"
            for opt in options:
                xml_input += f"    <option>{opt}</option>\n"
            xml_input += "  </options>\n"
        if placeholder:
            xml_input += f"  <placeholder>{placeholder}</placeholder>\n"
        xml_input += f"  <required>{str(required).lower()}</required>\n</ask_user>"

        input_params = {
            "message": xml_input
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

            # 3. Insert agent event in the DB
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
                    # result: {success: boolean, message: string}
                    
                    if not result.get("success", False):
                        return {
                            "success": False,
                            "output": result.get("message", "Error from client")
                        }

                    xml_response = result.get("message", "")
                    
                    # 5. Parse the XML response
                    try:
                        root = ET.fromstring(xml_response)
                        
                        selected = [s.text for s in root.findall("selected")]
                        user_input = root.find("input").text if root.find("input") is not None else None
                        skipped = root.find("skipped").text.lower() == "true" if root.find("skipped") is not None else False

                        parsed_result = {
                            "selected": selected,
                            "input": user_input,
                            "skipped": skipped
                        }

                        # Logic for required and skipped
                        if required and skipped:
                            # Stop the task? The tool itself returns the result, 
                            # the agent or supervisor should decide how to "stop" the task based on this.
                            # But I should tell the user.
                            return {
                                "success": False,
                                "output": "This input is required to proceed and cannot be skipped. stop the task."
                            }

                        final_result = {
                            "success": True,
                            "output": parsed_result
                        }

                        # 6. Update memory
                        assistant_message = f"Asked user: {question}"
                        update_memory(role="assistant", content=assistant_message, memory=self.memory)
                        update_memory(
                            role="tool",
                            name="ask_user",
                            tool_call_id=tool_call_id,
                            content=json.dumps(final_result),
                            memory=self.memory
                        )

                        return final_result

                    except ET.ParseError as pe:
                        return {
                            "success": False,
                            "output": f"Failed to parse user response XML: {str(pe)}. Response was: {xml_response}"
                        }

            return {
                "success": False,
                "output": f"Unexpected response type: {response_type}"
            }

        except Exception as e:
            return {
                "success": False,
                "output": f"Error executing ask_user: {str(e)}"
            }
