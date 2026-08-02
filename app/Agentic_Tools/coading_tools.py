from typing import Optional, Dict, Any, List
from langchain_core.language_models.chat_models import BaseChatModel
import json

from app.LLM.memory import Memory
from app.api.websocket_utils import send_ws_message
from app.Task.task_manager import task_manager
from app.helper import update_memory
from app.DB.Queries.agent_event import create_agent_event

class CoadingTools():
    def __init__(self, llm: BaseChatModel, task_id: str, chat_id: str, memory: Optional[Memory] = None):
        self.llm = llm
        self.task_id = task_id
        self.chat_id = chat_id
        self.memory = memory
        self.task_state = task_manager.get_state(self.task_id)
        self.websocket = self.task_state.websocket
        self.dbpool = self.task_state.dbpool

    async def grep(self, pattern: str, currentWorkDir: str, path: Optional[str] = None, include: Optional[str] = None, hide: Optional[str] = "false", tool_call_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Fast content search tool that works with any codebase size.
        Searches file contents using regular expressions.
        """
        input_params = {
            "pattern": pattern,
            "path": path,
            "currentWorkDir": currentWorkDir,
            "include": include,
            "hide": hide
        }

        try:
            # 1. Send websocket request to client
            await send_ws_message(
                websocket=self.websocket,
                type="client_tool_request",
                chat_id=self.chat_id,
                task_id=self.task_id,
                payload={
                    "tool": "grep",
                    "tool_call_id": tool_call_id,
                    "input": input_params,
                    "coming_from": "grep_tool_func/server"
                }
            )

            # 2. Insert agent event in the DB
            await create_agent_event(
                pool=self.dbpool,
                task_id=self.task_id,
                role="tool",
                message_type="client_tool_request",
                tool="grep",
                payload={"input": input_params},
                seq=self.task_state.get_next_seq()
            )

            # 3. Wait for client tool response
            tool_resp = await task_manager.wait_for_tool_response(self.task_id, tool_call_id)
            response_type = tool_resp.get("type")

            if response_type == "client_tool_response":
                payload = tool_resp.get("payload", {})
                if payload.get("tool") == "grep":
                    result = payload.get("result", {})
                    
                    # Grouping title, metadata, and message as tool_output
                    tool_output = {
                        "title": result.get("title", ""),
                        "metadata": result.get("metadata", {}),
                        "message": result.get("message", "")
                    }
                    
                    final_result = {
                        "success": result.get("success", False),
                        "output": tool_output
                    }

                    # 4. Update memory
                    assistant_message = f"Searching for pattern '{pattern}' in {path if path else 'project root'}"
                    update_memory(role="assistant", content=assistant_message, memory=self.memory)
                    update_memory(
                        role="tool",
                        name="grep",
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
                "output": f"Error executing grep: {str(e)}"
            }

    async def ls(self, currentWorkDir: str, path: Optional[str] = None, ignore: Optional[List[str]] = None, hide: Optional[str] = "false", tool_call_id: Optional[str] = None) -> Dict[str, Any]:
        """
        List directory contents tool.
        Returns a formatted string of files and directories.
        """
        input_params = {
            "path": path,
            "ignore": ignore,
            "currentWorkDir": currentWorkDir,
            "hide": hide
        }

        try:
            # 1. Send websocket request to client
            await send_ws_message(
                websocket=self.websocket,
                type="client_tool_request",
                chat_id=self.chat_id,
                task_id=self.task_id,
                payload={
                    "tool": "ls",
                    "tool_call_id": tool_call_id,
                    "input": input_params,
                    "coming_from": "ls_tool_func/server"
                }
            )

            # 2. Insert agent event in the DB
            await create_agent_event(
                pool=self.dbpool,
                task_id=self.task_id,
                role="tool",
                message_type="client_tool_request",
                tool="ls",
                payload={"input": input_params},
                seq=self.task_state.get_next_seq()
            )

            # 3. Wait for client tool response
            tool_resp = await task_manager.wait_for_tool_response(self.task_id, tool_call_id)
            response_type = tool_resp.get("type")

            if response_type == "client_tool_response":
                payload = tool_resp.get("payload", {})
                if payload.get("tool") == "ls":
                    result = payload.get("result", {})
                    
                    # Grouping title, metadata, and message as tool_output
                    tool_output = {
                        "title": result.get("title", ""),
                        "metadata": result.get("metadata", {}),
                        "message": result.get("message", "")
                    }
                    
                    final_result = {
                        "success": result.get("success", False),
                        "output": tool_output
                    }

                    # 4. Update memory
                    assistant_message = f"Listing contents of directory: {path}"
                    update_memory(role="assistant", content=assistant_message, memory=self.memory)
                    update_memory(
                        role="tool",
                        name="ls",
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
                "output": f"Error executing ls: {str(e)}"
            }

    async def glob(self, pattern: List[str], path: str, currentWorkDir: str, hide: Optional[str] = "false", tool_call_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Search for files using glob patterns.
        """
        input_params = {
            "pattern": pattern,
            "path": path,
            "currentWorkDir": currentWorkDir,
            "hide": hide
        }

        try:
            # 1. Send websocket request to client
            await send_ws_message(
                websocket=self.websocket,
                type="client_tool_request",
                chat_id=self.chat_id,
                task_id=self.task_id,
                payload={
                    "tool": "glob",
                    "tool_call_id": tool_call_id,
                    "input": input_params,
                    "coming_from": "glob_tool_func/server"
                }
            )

            # 2. Insert agent event in the DB
            await create_agent_event(
                pool=self.dbpool,
                task_id=self.task_id,
                role="tool",
                message_type="client_tool_request",
                tool="glob",
                payload={"input": input_params},
                seq=self.task_state.get_next_seq()
            )

            # 3. Wait for client tool response
            tool_resp = await task_manager.wait_for_tool_response(self.task_id, tool_call_id)
            response_type = tool_resp.get("type")

            if response_type == "client_tool_response":
                payload = tool_resp.get("payload", {})
                if payload.get("tool") == "glob":
                    result = payload.get("result", {})
                    
                    # Grouping title, metadata, and message as tool_output
                    tool_output = {
                        "title": result.get("title", ""),
                        "metadata": result.get("metadata", {}),
                        "message": result.get("message", "")
                    }
                    
                    final_result = {
                        "success": result.get("success", False),
                        "output": tool_output
                    }

                    # 4. Update memory
                    assistant_message = f"Searching for files with patterns {pattern} in {path}"
                    update_memory(role="assistant", content=assistant_message, memory=self.memory)
                    update_memory(
                        role="tool",
                        name="glob",
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
                "output": f"Error executing glob: {str(e)}"
            }

