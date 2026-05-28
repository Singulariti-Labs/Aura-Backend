import os
from typing import Optional
from langchain_core.language_models.chat_models import BaseChatModel
from app.LLM.memory import Memory
from app.Task.task_manager import task_manager
from app.api.websocket_utils import send_ws_message
from app.helper import update_memory, save_tool_response
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

    async def read_skill(self, skill_name: str, path: str, arguments: Optional[dict] = None, tool_call_id: Optional[str] = None):
        """
        Logic to read a skill from the filesystem (default) or client (local).
        """
        # Resolve path
        if path.startswith("default_skill"):
            current_dir = os.path.dirname(os.path.abspath(__file__))
            # app/Agentic_Tools -> app/Skills
            skills_root = os.path.abspath(os.path.join(current_dir, "..", "Skills"))
            
            # Extract the part after 'default_skill/'
            if path == "default_skill":
                skill_path = os.path.join(skills_root, skill_name)
                rel_path = skill_name
            else:
                # Extract the part after 'default_skill/'
                rel_path = path.replace("default_skill/", "").replace("default_skill", "")
                if rel_path.startswith("/") or rel_path.startswith("\\"):
                    rel_path = rel_path[1:]
                
                # If rel_path starts with "Skills/", strip it to avoid duplication with skills_root
                if rel_path.startswith("Skills/") or rel_path.startswith("Skills\\"):
                    rel_path = rel_path[7:]
                elif rel_path == "Skills":
                    rel_path = ""
                    
                skill_path = os.path.join(skills_root, rel_path)

            skill_md_path = os.path.join(skill_path, "SKILL.md")

            if not os.path.exists(skill_md_path):
                return {
                    "is_error": True,
                    "message": f"Skill '{skill_name}' not found at {skill_path}. Make sure the skill name and path are correct."
                }

            try:
                with open(skill_md_path, "r", encoding="utf-8") as f:
                    content = f.read()
                
                output = f"## Skill: {skill_name}\n\n**Base directory**: default_skill/{rel_path}\n\n{content.strip()}"
                return {
                    "title": f"Loaded skill: {skill_name}",
                    "output": output,
                    "metadata": {
                        "name": skill_name,
                        "dir": skill_path,
                    },
                }
            except Exception as e:
                return {
                    "is_error": True,
                    "message": f"Failed to read skill '{skill_name}': {str(e)}"
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
                tool_resp = await task_manager.wait_for_input(self.task_id)

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
