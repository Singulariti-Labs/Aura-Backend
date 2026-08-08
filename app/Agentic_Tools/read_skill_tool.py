import json
import os
from typing import Optional
from langchain_core.language_models.chat_models import BaseChatModel
from app.LLM.memory import Memory
from app.Task.task_manager import task_manager
from app.api.websocket_utils import send_ws_message
from app.helper import update_memory
from app.DB.Queries.agent_event import create_agent_event


class SkillLoader:
    def __init__(self, llm: BaseChatModel, task_id: str, chat_id: str, memory: Optional[Memory] = None):
        self.llm = llm
        self.task_id = task_id
        self.chat_id = chat_id
        self.memory = memory
        self.shared_memory = memory
        self.task_state = task_manager.get_state(self.task_id)
        self.websocket = self.task_state.websocket
        self.dbpool = self.task_state.dbpool

    async def _send_default_skill_response(self, tool_call_id: Optional[str], result: dict):
        """
        Default skills are read on the server, so the server must emit the tool
        response that the client normally sends for local skills.
        """
        await send_ws_message(
            websocket=self.websocket,
            type="server_tool_response",
            chat_id=self.chat_id,
            task_id=self.task_id,
            payload={
                "tool": "read_skill",
                "tool_call_id": tool_call_id,
                "result": result,
                "coming_from": "read_skill_tool_func/server"
            }
        )

        # Persist the same server-side tool response for replay/session history.
        try:
            await create_agent_event(
                pool=self.dbpool,
                task_id=self.task_id,
                role="tool",
                message_type="server_tool_response",
                tool="read_skill",
                payload={
                    "tool_call_id": tool_call_id,
                    "result": result
                },
                seq=self.task_state.get_next_seq()
            )
        except Exception as e:
            print(f"Failed to persist default read_skill response: {e}")

        # Keep local model memory aligned with the assistant tool call.
        if self.shared_memory is not None:
            try:
                content = result.get("content", "")
                update_memory(role="assistnat", name="read_skill", content=content)
                update_memory(
                    role="tool",
                    name="read_skill",
                    tool_call_id=tool_call_id,
                    content=content,
                    memory=self.shared_memory
                )
            except Exception as e:
                print(f"Failed to update memory for default read_skill response: {e}")

    async def read_skill(self, skill_name: str, path: str, arguments: Optional[dict] = None, tool_call_id: Optional[str] = None):
        """
        Logic to read a skill from the filesystem (default) or client (local).
        """
        if path.startswith("default_skill"):
            try:
                current_dir = os.path.dirname(os.path.abspath(__file__))
                # app/Agentic_Tools -> app/Skills
                skills_root = os.path.abspath(os.path.join(current_dir, "..", "Skills"))

                # Resolve either path="default_skill" + skill_name, or path="default_skill/<skill>".
                if path == "default_skill":
                    rel_path = skill_name
                else:
                    rel_path = path.replace("default_skill/", "").replace("default_skill", "")
                    if rel_path.startswith("/") or rel_path.startswith("\\"):
                        rel_path = rel_path[1:]

                    # Avoid app/Skills/Skills/<name> when the model includes Skills/.
                    if rel_path.startswith("Skills/") or rel_path.startswith("Skills\\"):
                        rel_path = rel_path[7:]
                    elif rel_path == "Skills":
                        rel_path = ""

                skill_path = os.path.abspath(os.path.join(skills_root, rel_path))
                validated_skill_name = os.path.basename(os.path.normpath(skill_path)) or skill_name

                # Default skills must stay inside app/Skills.
                if os.path.commonpath([skills_root, skill_path]) != skills_root:
                    error_message = f"Skill '{skill_name}' resolved outside the default skills directory."
                    await self._send_default_skill_response(
                        tool_call_id=tool_call_id,
                        result={
                            "success": False,
                            "message": error_message
                        }
                    )
                    return {
                        "is_error": True,
                        "message": error_message
                    }

                skill_md_path = os.path.join(skill_path, "SKILL.md")

                if not os.path.exists(skill_md_path):
                    error_message = f"Skill '{skill_name}' not found at {skill_path}. Make sure the skill name and path are correct."
                    await self._send_default_skill_response(
                        tool_call_id=tool_call_id,
                        result={
                            "success": False,
                            "message": error_message
                        }
                    )
                    return {
                        "is_error": True,
                        "message": error_message
                    }

                with open(skill_md_path, "r", encoding="utf-8") as f:
                    content = f.read()

                await self._send_default_skill_response(
                    tool_call_id=tool_call_id,
                    result={
                        "success": True,
                        "name": validated_skill_name,
                        "content": content,
                        "path": "default_skill"
                    }
                )

                output = f"## Skill: {validated_skill_name}\n\n**Base directory**: default_skill/{rel_path}\n\n{content.strip()}"
                return {
                    "title": f"Loaded skill: {validated_skill_name}",
                    "output": output,
                    "metadata": {
                        "name": validated_skill_name,
                        "dir": skill_path,
                    },
                }
            except Exception as e:
                error_message = f"Failed to execute read_skill for default skill '{skill_name}': {str(e)}"
                try:
                    await self._send_default_skill_response(
                        tool_call_id=tool_call_id,
                        result={
                            "success": False,
                            "message": error_message
                        }
                    )
                except Exception as send_error:
                    error_message = f"{error_message}; failed to send server_tool_response: {send_error}"

                return {
                    "is_error": True,
                    "message": error_message
                }
        else:
            # Local Skill Handling via WebSocket
            try:
                await send_ws_message(
                    websocket=self.websocket,
                    type="client_tool_request",
                    chat_id=self.chat_id,
                    task_id=self.task_id,
                    payload={
                        "tool": "read_skill",
                        "tool_call_id": tool_call_id,
                        "input": {
                            "skill_name": skill_name,
                            "path": path,
                            "arguments": arguments,
                            "hide": "true"
                        },
                        "coming_from": "read_skill_tool_func/server"
                    }
                )

                # Insert AURA complex agent event in the DB
                await create_agent_event(
                    pool=self.dbpool,
                    task_id=self.task_id,
                    role="tool",
                    message_type="client_tool_request",
                    tool="read_skill",
                    payload={
                        "input": {
                            "skill_name": skill_name,
                            "path": path,
                            "arguments": arguments,
                            "hide": "true"
                        }
                    },
                    seq=self.task_state.get_next_seq()
                )

                # Wait for client tool response
                tool_resp = await task_manager.wait_for_tool_response(self.task_id, tool_call_id)

                response_type = tool_resp.get("type")
                if response_type == "client_tool_response":
                    payload = tool_resp.get("payload", {})

                    if payload.get("tool") == "read_skill":
                        result = payload.get("result")

                        if result.get("success") == True:
                            content = result.get("content", "")
                            # For local skills, rel_path is just the path provided
                            output = f"## Skill: {skill_name}\n\n**Base directory**: {path}\n\n{content.strip()}"

                            final_result = {
                                "title": f"Loaded skill: {skill_name}",
                                "output": output,
                                "metadata": {
                                    "name": skill_name,
                                    "dir": path,
                                },
                            }
                            return final_result

                return {
                    "is_error": True,
                    "message": f"Skill '{skill_name}' not loaded properly or not found at given path: {path}."
                }
            except Exception as e:
                return {
                    "is_error": True,
                    "message": f"Error while loading local skill: {str(e)}"
                }
