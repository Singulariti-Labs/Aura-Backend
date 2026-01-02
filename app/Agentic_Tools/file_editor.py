from typing import Optional
from langchain_core.language_models.chat_models import BaseChatModel

from app.LLM.memory import Memory, Message
from app.api.websocket_utils import send_ws_message
from app.Task.task_manager import task_manager
from app.helper import update_memory, save_tool_response
from app.DB.Queries.agent_event import create_agent_event
from openai import AsyncOpenAI



import asyncio
import json
import re
import os
import openai
import litellm

from dotenv import load_dotenv

load_dotenv()

class FileEditor():
    
    def __init__(self,  llm: BaseChatModel, task_id: str, chat_id: str, memory: Optional[Memory] = None, maxTokens: int = 128000):

        self.llm = llm
        self.task_id = task_id
        self.chat_id = chat_id
        self.max_tokens = maxTokens
        self.shared_memory = memory
        self.task_state = task_manager.get_state(self.task_id)
        self.websocket = self.task_state.websocket
        self.dbpool = self.task_state.dbpool
    
    async def create_file(self, path: str, content: str, permissions: str = "644", tool_call_id: Optional[str] = None):
        """Create File Tool which creates file at the given path"""
        try:
            await send_ws_message(
                websocket= self.websocket,
                type="client_tool_request",
                chat_id=self.chat_id,
                task_id=self.task_id,
                payload={
                    "tool": "create_file",
                    "input": {
                        "path": path,
                        "content": content,
                        "permissions": permissions
                    }
                }
            )

            # Insert AURA complex agent event in the DB 
            await create_agent_event(
                pool=self.dbpool,
                task_id=self.task_id,
                role="tool",
                message_type="client_tool_request",
                tool="create_file",
                payload= {
                  "input": {
                        "path": path,
                        "content": content,
                        "permissions": permissions
                },
            },
                seq = self.task_state.get_next_seq()
            )

            # Wait for client tool response 
            tool_resp = await task_manager.wait_for_input(self.task_id)

            response_type = tool_resp.get("type")

            if response_type == "client_tool_response":
                payload = tool_resp.get("payload", {})

            # check if correct tool responded
            if payload.get("tool") == "create_file":
                result = payload.get("result")
                
                if result.get("success") == True:
                    final_result = {
                        "success": True,
                        "output": result.get("message", f"File '{path}' created successfully."),
                    }
                else:
                    final_result = {
                        "success": False,
                        "output": result.get("message", "Unknown error while creating file."),
                    }
                
                # Update local memory with the conversation - WIP** store Message Neatly in memory to track previous task properly
                arguments = {"path": path, "content": content, "permissions": permissions}
                assistant_message = f"lets create a file using create_file tool args {json.dumps(arguments)}"
                update_memory(role="assistant", content=assistant_message, memory=self.shared_memory)
                update_memory(role="tool", name="create_file", tool_call_id=tool_call_id, content=json.dumps(final_result), memory=self.shared_memory)

                # Save tool response
                save_tool_response(
                    task_id=self.task_id,
                    tool_name="create_file",
                    response={
                    "input": {
                        "path": path,
                        "content": content,
                        "permissions": permissions
                    },
                    "result": final_result
                })

                return final_result

            # If response type not correct
            return {
                "success": False,
                "output": f"Unexpected response type: {response_type}",
            }
        except Exception as e:
            return { "success": False, "output": f"Error in creating file: {str(e)}"}

    async def str_replace(self, path: str, new_str: str, old_str: str, tool_call_id: Optional[str] = None):
        """Replaced string {old_str} with {new_str}"""
        try:
            await send_ws_message(
                websocket= self.websocket,
                type="client_tool_request",
                chat_id=self.chat_id,
                task_id=self.task_id,
                payload={
                    "tool": "str_replace",
                    "input": {
                        "path": path,
                        "new_str": new_str,
                        "old_str": old_str
                    } 
                }
            )

            # Insert AURA complex agent event in the DB 
            await create_agent_event(
                pool=self.dbpool,
                task_id=self.task_id,
                role="tool",
                message_type="client_tool_request",
                tool="str_replace",
                payload= {
                  "input": {
                        "path": path,
                        "new_str": new_str,
                        "old_str": old_str
                },
            },
                seq = self.task_state.get_next_seq()
            )

            # Wait for client tool response
            tool_resp = await task_manager.wait_for_input(self.task_id)

            response_type = tool_resp.get("type")

            if response_type == "client_tool_response":
                payload = tool_resp.get("payload", {})

            # check if correct tool responded
            if payload.get("tool") == "str_replace":
                result = payload.get("result")
                
                if result.get("success") == True:
                    final_result = {
                        "success": True,
                        "output": result.get("message", f"String '{old_str}' is replaced by '{new_str}' in file '{path}' successfully."),
                    }
                else:
                    final_result = {
                        "success": False,
                        "output": result.get("message", f"Unknown error while replacing string in a file '{path}'."),
                    }
                
                # Update local memory with the conversation - WIP** store Message Neatly in memory to track previous task properly
                arguments = {"path": path, "new_str": new_str, "old_str": old_str}
                assistant_message = f"Replace a string in file using str_replace tool args {json.dumps(arguments)}"
                update_memory(role="assistant", content=assistant_message, memory=self.shared_memory)
                update_memory(role="tool", name="str_replace", tool_call_id=tool_call_id, content=json.dumps(final_result), memory=self.shared_memory)

                # Save tool response
                save_tool_response(
                    task_id=self.task_id,
                    tool_name="str_replace",
                    response={
                    "input": {
                        "path": path,
                        "new_str": new_str,
                        "old_str": old_str
                    },
                    "result": final_result
                })

                return final_result

            # If response type not correct
            return {
                "success": False,
                "output": f"Unexpected response type: {response_type}",
            }
        except Exception as e:
            return { "success": False, "output": f"Error in replacing string: {str(e)}"}

    async def rewrite_file(self, path: str, content: str, permissions: str="644", tool_call_id: Optional[str] = None):
        """Rewriting full file with content"""
        try:
            await send_ws_message(
                websocket= self.websocket,
                type="client_tool_request",
                chat_id=self.chat_id,
                task_id=self.task_id,
                payload={
                    "tool": "rewrite_file",
                    "input": {
                        "path": path,
                        "content": content,
                        "permissions": permissions
                    } 
                }
            )

            # Insert AURA complex agent event in the DB 
            await create_agent_event(
                pool=self.dbpool,
                task_id=self.task_id,
                role="tool",
                message_type="client_tool_request",
                tool="rewrite_file",
                payload= {
                  "input": {
                        "path": path,
                        "content": content,
                        "permissions": permissions
                    }
                },
                seq = self.task_state.get_next_seq()
            )

            # Wait for client tool response
            tool_resp = await task_manager.wait_for_input(self.task_id)

            response_type = tool_resp.get("type")

            if response_type == "client_tool_response":
                payload = tool_resp.get("payload", {})

            # check if correct tool responded
            if payload.get("tool") == "rewrite_file":
                result = payload.get("result")
                
                if result.get("success") == True:
                    final_result = {
                        "success": True,
                        "output": result.get("message", f"File '{path}' rewrited successfully."),
                    }
                else:
                    final_result = {
                        "success": False,
                        "output": result.get("message", f"Unknown error while rewriting file '{path}'."),
                    }
                
                # Update local memory with the conversation - WIP** store Message Neatly in memory to track previous task properly
                arguments = {"path": path, "content": content, "permissions": permissions}
                assistant_message = f"Rewriting full file using rewrite_file tool args {json.dumps(arguments)}"
                update_memory(role="assistant", content=assistant_message, memory=self.shared_memory)
                update_memory(role="tool", name="rewrite_file", tool_call_id=tool_call_id, content=json.dumps(final_result), memory=self.shared_memory)

                # Save tool response
                save_tool_response(
                    task_id=self.task_id,
                    tool_name="rewrite_file",
                    response={
                    "input": {
                        "path": path,
                        "content": content,
                        "permissions": permissions
                    },
                    "result": final_result
                })
                
                return final_result

            # If response type not correct
            return {
                "success": False,
                "output": f"Unexpected response type: {response_type}",
            }
        except Exception as e:
            return { "success": False, "output": f"Error in rewriting file: {str(e)}"}
    
    async def delete_file(self, path:str, tool_call_id: Optional[str] = None):
        """Deleting file located at path {path}"""
        try:
            await send_ws_message(
                websocket= self.websocket,
                type="client_tool_request",
                chat_id=self.chat_id,
                task_id=self.task_id,
                payload={
                    "tool": "delete_file",
                    "input": {
                        "path": path
                    } 
                }
            )

            # Insert AURA complex agent event in the DB 
            await create_agent_event(
                pool=self.dbpool,
                task_id=self.task_id,
                role="tool",
                message_type="client_tool_request",
                tool="delete_file",
                payload= {
                  "input": {
                        "path": path
                },
            },
                seq = self.task_state.get_next_seq()
            )

            # Wait for client tool response
            tool_resp = await task_manager.wait_for_input(self.task_id)

            response_type = tool_resp.get("type")

            if response_type == "client_tool_response":
                payload = tool_resp.get("payload", {})

            # check if correct tool responded
            if payload.get("tool") == "delete_file":
                result = payload.get("result")
                
                if result.get("success") == True:
                    final_result = {
                        "success": True,
                        "output": result.get("message", f"File '{path}' deleted successfully."),
                    }
                else:
                    final_result = {
                        "success": False,
                        "output": result.get("message", f"Unknown error while deleting file '{path}'."),
                    }
                
                # Update local memory with the conversation - WIP** store Message Neatly in memory to track previous task properly
                arguments = {"path": path}
                assistant_message = f"deleating file using delete_file tool args {json.dumps(arguments)}"
                update_memory(role="assistant", content=assistant_message, memory=self.shared_memory)
                update_memory(role="tool", name="delete_file", tool_call_id=tool_call_id, content=json.dumps(final_result), memory=self.shared_memory)

                # Save tool response
                save_tool_response(
                    task_id=self.task_id,
                    tool_name="delete_file",
                    response={
                    "input": {
                        "path": path
                    },
                    "result": final_result
                })

                return final_result

            # If response type not correct
            return {
                "success": False,
                "output": f"Unexpected response type: {response_type}",
            }
        except Exception as e:
            return { "success": False, "output": f"Error while deleting file: {str(e)}"}
    
    async def insert_str(self, path:str, insert_line_no: int, new_str: str, tool_call_id: Optional[str] = None):
        """Inserting string {new_str} at the line number {insert_line}"""
        try:
            await send_ws_message(
                websocket= self.websocket,
                type="client_tool_request",
                chat_id=self.chat_id,
                task_id=self.task_id,
                payload={
                    "tool": "insert_str",
                    "input": {
                        "path": path,
                        "insert_line_no": insert_line_no,
                        "new_str": new_str
                    } 
                }
            )

            # Insert AURA complex agent event in the DB 
            await create_agent_event(
                pool=self.dbpool,
                task_id=self.task_id,
                role="tool",
                message_type="client_tool_request",
                tool="insert_tool",
                payload= {
                  "input": {
                        "path": path,
                        "insert_line_no": insert_line_no,
                        "new_str": new_str
                },
            },
                seq = self.task_state.get_next_seq()
            )

            

            # Wait for client tool response
            tool_resp = await task_manager.wait_for_input(self.task_id)

            response_type = tool_resp.get("type")

            if response_type == "client_tool_response":
                payload = tool_resp.get("payload", {})

            # check if correct tool responded
            if payload.get("tool") == "insert_str":
                result = payload.get("result")
                
                if result.get("success") == True:
                    final_result = {
                        "success": True,
                        "output": result.get("message", f"String '{new_str}' inserted successfully in File '{path}'."),
                    }
                else:
                    final_result = {
                        "success": False,
                        "output": result.get("message", f"Unknown error while inserting string in '{path}'."),
                    }
                
                # Update local memory with the conversation - WIP** store Message Neatly in memory to track previous task properly
                arguments = {"path": path}
                assistant_message = f"Inserting string in file using insert_str tool args {json.dumps(arguments)}"
                update_memory(role="assistant", content=assistant_message, memory=self.shared_memory)
                update_memory(role="tool", name="insert_str", tool_call_id=tool_call_id, content=json.dumps(final_result), memory=self.shared_memory)

                # Save tool response
                save_tool_response(
                    task_id=self.task_id,
                    tool_name="insert_str",
                    response={
                    "input": {
                        "path": path,
                        "insert_line_no": insert_line_no,
                        "new_str": new_str
                    },        
                    "result": final_result
                    }
                )
                
                return final_result

            # If response type not correct
            return {
                "success": False,
                "output": f"Unexpected response type: {response_type}",
            }
        except Exception as e:
            return { "success": False, "output": f"Error while inserting string in file: {str(e)}"}
    


    async def edit_file(self, target_file: str, instructions: str, code_edit: str, tool_call_id: Optional[str] = None):
        """Edit a file by sending edit instructions to client via websocket"""
        
        new_content = None
        error_message = None
        try:
            original_content = await self.get_file_content(target_file= target_file)

            if original_content:
                new_content, error_message = await self._call_ai_editor(file_content=original_content, code_edit=code_edit, instructions=instructions, path=target_file)
                # print(f"📂EDIT_FILE_LOG: 🎆Orignal_Content:{original_content}, 🆕New_Content:{new_content}, ❌Error_Message:{error_message}")
                if error_message:
                    final_result = {
                    "success": False,
                    "output": ({
                        "message": f"AI editing failed: {error_message}",
                        "path": target_file,
                        "original_content": original_content,
                        "updated_content": None
                    })
                } 
                elif new_content is None:
                    final_result = {
                    "success": False,
                    "output": ({
                        "message": "AI editing failed for an unknown reason. The model returned no content.",
                        "path": target_file,
                        "original_content": original_content,
                        "updated_content": None
                    })
                }
                elif new_content == original_content:
                    final_result = {
                    "success": True,
                    "output": ({
                        "message": f"AI editing resulted in no changes to the file '{target_file}'.",
                        "path": target_file,
                        "original_content": original_content,
                        "updated_content": original_content
                    })
                }
                else:
                    await send_ws_message(
                        websocket= self.websocket,
                        type="client_tool_request",
                        chat_id=self.chat_id,
                        task_id=self.task_id,
                        payload={
                            "tool": "edit_file",
                            "input": {
                                "path": target_file,
                                "original_content": original_content,
                                "updated_content": new_content
                            } 
                        }
                    )

                    # Insert AURA complex agent event in the DB 
                    await create_agent_event(
                        pool=self.dbpool,
                        task_id=self.task_id,
                        role="tool",
                        message_type="client_tool_request",
                        tool="edit_file",
                        payload= {
                            "input": {
                                "path": target_file,
                                "orignal_content": original_content,
                                "updated_content": new_content
                            }
                        },
                        seq = self.task_state.get_next_seq()
                    )

                    
                
                    # Wait for client tool response
                    tool_resp = await task_manager.wait_for_input(self.task_id)

                    response_type = tool_resp.get("type")

                    if response_type == "client_tool_response":
                        payload = tool_resp.get("payload", {})

                    # check if correct tool responded
                    if payload.get("tool") == "edit_file":
                        result = payload.get("result")
                    
                        if result.get("success") == True:
                            final_result = {
                                "success": True,
                                "output": ({
                                    "message": f"File '{target_file}' edited successfully.",
                                    "path": target_file,
                                    "original_content": original_content,
                                    "updated_content": new_content
                                })
                            }
                        else:
                            final_result = {
                                "success": False,
                                "output": ({
                                    "message": f"File '{target_file}' failed to make any changes successfully.",
                                    "path": target_file,
                                    "original_content": original_content,
                                    "updated_content": new_content
                                })
                            }
                
            else:
                final_result = {"success": False, "output": f"Failed to get the content of the file {target_file} to edit"}

            
            arguments = {
                    "instructions": instructions,
                    "path": target_file,
                    "code_edit": code_edit
                }
            assistant_message = f"Editing the File '{target_file}' using edit_file args {json.dumps(arguments)}"
            update_memory(role="assistant", content=assistant_message, memory=self.shared_memory)
            update_memory(role="tool", name="edit_file", tool_call_id=tool_call_id, content=json.dumps(final_result), memory=self.shared_memory)

            # Save tool response
            save_tool_response(
                task_id=self.task_id,
                tool_name="edit_file",
                response={
                    "input": {
                    "path": target_file,
                    "original_content": original_content,
                    "updated_content": new_content
                },     
                "result": final_result
                }
            )
            
            return final_result
        
        except Exception as e:
            return { "success": False, "output": f"Unknown error while editing file: {str(e)}"}

    async def get_file_content(self, target_file: str):
        """Get the content of the file from client"""
        try:
            await send_ws_message(
                websocket= self.websocket,
                type="client_tool_request",
                chat_id=self.chat_id,
                task_id=self.task_id,
                payload={
                    "tool": "get_file_content",
                    "input": {
                        "path": target_file,
                    } 
                }
            )

            # Insert AURA complex agent event in the DB 
            await create_agent_event(
                pool=self.dbpool,
                task_id=self.task_id,
                role="tool",
                message_type="client_tool_request",
                tool="get_file_content",
                payload= {
                    "input": {
                        "path": target_file
                    }
                },
                seq = self.task_state.get_next_seq()
            )

            

            # Wait for client tool response
            tool_resp = await task_manager.wait_for_input(self.task_id)

            response_type = tool_resp.get("type")

            if response_type == "client_tool_response":
                payload = tool_resp.get("payload", {})

            # check if correct tool responded
            if payload.get("tool") == "get_file_content":
                result = payload.get("result")
                
                if result.get("success") == True:
                    content = result.get("original_content", result.get("content")) # Look for "orignal_content" if not avilabel fallback for "content".
                    return content
                else:
                    return None

        except Exception as e:
            print(f"Error while getting the file content from file {target_file}: Error {e}")
            return None


    async def _call_ai_editor(self, file_content: str, path: str, instructions: str, code_edit: str):
        """Call Morph API to apply edits to file content.
            Returns a tuple (new_content, error_message).
            On success, error_message is None.
            On failure, new_content is None.
        """

        try:
            morph_api_key = os.getenv('MORPH_API_KEY')
            openrouter_key = os.getenv('OPENROUTER_API_KEY')
            
            messages = [{
                "role": "user", 
                "content": f"<instruction>{instructions}</instruction>\n<code>{file_content}</code>\n<update>{code_edit}</update>"
            }]

            response = None
            if morph_api_key:
                client = openai.AsyncOpenAI(
                    api_key=morph_api_key,
                    base_url="https://api.morphllm.com/v1"
                )
                response = await client.chat.completions.create(
                    model="morph-v3-large",
                    messages=messages,
                    temperature=0.0,
                    timeout=30.0
                )
            elif openrouter_key:
                response = await litellm.acompletion(
                    model="openrouter/morph/morph-v3-large",
                    messages=messages,
                    api_key=openrouter_key,
                    api_base="https://openrouter.ai/api/v1",
                    temperature=0.0,
                    timeout=30.0
                )
            else:
                error_msg = "No Morph or OpenRouter API key found, cannot perform AI edit."
                return None, error_msg
            
            if response and response.choices and len(response.choices) > 0:
                content = response.choices[0].message.content.strip()

                # Extract code block if wrapped in markdown
                if content.startswith("```") and content.endswith("```"):
                    lines = content.split('\n')
                    if len(lines) > 2:
                        content = '\n'.join(lines[1:-1])
                
                return content, None
            else:
                error_msg = f"Invalid response from Morph/OpenRouter API: {response}"
                return None, error_msg
                
        except Exception as e:
            error_message = f"AI model call for file edit failed. Exception: {str(e)}"
            # Try to get more details from the exception if it's an API error
            if hasattr(e, 'response') and hasattr(e.response, 'text'):
                error_message += f"\n\nAPI Response Body:\n{e.response.text}"
            elif hasattr(e, 'body'): # litellm sometimes puts it in body
                error_message += f"\n\nAPI Response Body:\n{e.body}"
            return None, error_message