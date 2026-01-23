from fastapi import WebSocket
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain.agents import create_agent
from asyncpg import Pool
from typing import Optional

from app.LLM.memory import Memory
from app.Types.context_type import IDEContextState
from app.api.websocket_utils import send_ws_message
from app.DB.Queries.agent_event import create_agent_event
from app.DB.Queries.task import update_task_status
from app.Prompts.ide_app_prompt import IDE_AGENT_PROMPT
from app.Prompts.browser_page_option import BROWSER_APP_PROMPT
from app.Tools.Foreground_App_Tools.web_search_tool import web_search_tool
from app.Tools.Foreground_App_Tools.web_scraping_tool import web_scraping_tool


import asyncpg


class ContextAgent():

    def __init__(
        self,
        query: str,
        payload: dict,
        llm: BaseChatModel,
        task_id: str,
        chat_id: str,
        dbpool: Pool,
        screenshot: Optional[str] = None,
        memory: Optional[Memory] = None,
        websocket: Optional[WebSocket] = None,
        llm_provider: str = "openai",
        maxTokens: int = 128000
    ):
        self.query = query
        self.payload = payload
        self.llm = llm
        self.task_id = task_id
        self.chat_id = chat_id
        self.dbpool = dbpool
        self.screenshot = screenshot
        self.llm_provider = llm_provider
        self.max_tokens = maxTokens
        self.shared_memory = memory
        self.websocket = websocket

    async def run_foreground_app_agent(self):
        """
        Run the foreground app agent to extract information from the foreground app and answer the user query
        """
        try:
            app_context = None
            app_type = self.payload.get("app_type")

            print("RUNNING FOREGROUND APP AGENT")
            if app_type == "ide":
                result = await self.run_ide_agent()
            else:
                result = await self.run_browser_agent()
            
            return result
            
        except Exception as e:
            raise RuntimeError(f"Error running foreground app agent: {str(e)}")
            return None
        
            
    async def run_ide_agent(self):
        """
        Run the IDE agent to give answer to the user query realted to ide running in the foreground
        """
        try:

            app_details = self.payload.get("app_details")
            app_type = self.payload.get("app_type")
            app_name = app_details.get("name")
            active_file = app_details.get("active_file")
            file_content = app_details.get("active_file_content")

            formatted_system_prompt = IDE_AGENT_PROMPT.format(
                app_name=app_name,
                app_type=app_type,
                active_file=active_file,
                file_content=file_content
            )

            print("LLM: ", self.llm)
            print("QUERY: ", self.query)

            tools = [web_search_tool]

            agent = create_agent(
                model=self.llm,
                tools=tools,
                system_prompt=formatted_system_prompt
            )

            input = {
                "messages": [
                    ("user", self.query)
                ]
            }

            # response = agent.invoke(input)
            # print("IDE_AGENT_RESULT: ", response)
            # return response

             # Use astream with stream_mode="messages" for token-by-token streaming
             # This returns a generator of (message_chunk, metadata)
            full_response = ""
            async for chunk, metadata in agent.astream(input, stream_mode="messages"):
                # Check if the chunk is an AIMessageChunk (actual LLM content)
                if chunk.content:
                    print(chunk.content, end="", flush=True)
                    full_response += chunk.content
                    
                    # Stream to your websocket if available
                    await send_ws_message(
                        websocket=self.websocket,
                        type="aura_message",
                        task_id=self.task_id,
                        chat_id=self.chat_id,
                        payload={
                            "content": {
                                "role": "assistant",
                                "tool": "foreground_app",
                                "message": chunk.content,
                            }
                        },
                    )

            print("\nIDE_AGENT_RESULT: ", full_response)
            
            # creating the agent event to store the response in db
            await create_agent_event(
                pool=self.dbpool,
                task_id=self.task_id,
                role="assistant",
                message_type="aura_message",
                tool="foreground_app",
                payload={
                    "content": {
                        "message": full_response
                    },
                },
            )

            # updating the task status to completed
            await update_task_status(self.dbpool, self.task_id, "completed")

            return full_response

        except Exception as e:
            raise RuntimeError(f"Error running IDE agent: {str(e)}")
            return None

    async def run_browser_agent(self):
        """
        Run the browser agent to give answer to the user query realted to browser running in the foreground
        """
        try:

            app_details = self.payload.get("app_details") or {}
            app_type = self.payload.get("app_type", None)
            app_name = app_details.get("name", None)
            url = app_details.get("url", None)
            title = app_details.get("title", None)

            formatted_system_prompt = BROWSER_APP_PROMPT.format(
                app_name=app_name,
                app_type=app_type,
                url=url,
                title=title
            )

            tools = [web_search_tool, web_scraping_tool]

            agent = create_agent(
                model=self.llm,
                tools=tools,
                system_prompt=formatted_system_prompt
            )

            input = {
                "messages": [
                    ("user", self.query)
                ]
            }

            # response = agent.invoke(input)
            # print("IDE_AGENT_RESULT: ", response)
            # return response

            ### --------------------  Streaming Response Token by Token without events ------------------------
            
            # Use astream with stream_mode="messages" for token-by-token streaming
            # This returns a generator of (message_chunk, metadata)
            full_response = ""
            # async for chunk, metadata in agent.astream(input, stream_mode="messages"):
            #     # Check if the chunk is an AIMessageChunk (actual LLM content)
            #     if chunk.content:
            #         print(chunk.content, end="", flush=True)
            #         full_response += chunk.content
                    
            #         # Stream to your websocket if available
            #         await send_ws_message(
            #             websocket=self.websocket,
            #             type="aura_message",
            #             task_id=self.task_id,
            #             chat_id=self.chat_id,
            #             payload={
            #                 "content": {
            #                     "role": "assistant",
            #                     "tool": "foreground_app",
            #                     "message": chunk.content,
            #                 }
            #             },
            #         )


            ### ------------------------  Streaming Response With Events ------------------------
            active_tool_calls = {} 
            async for chunk in agent.astream(input, stream_mode="updates"):

                for node_name, data in chunk.items():
                    last_message = data['messages'][-1]
            
                # === TOOL CALL PHASE ===
                if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
                    for tc in last_message.tool_calls:

                        tool_call_id = tc["id"]
                        tool_name = tc["name"]
                        tool_input = tc["args"]

                        # Store mapping for later lookup
                        active_tool_calls[tool_call_id] = {
                            "tool_name": tool_name,
                            "input": tool_input,
                        }

                        print(f"🔧 Tool: {tool_name}")
                        print(f"📝 Input: {tool_input}")
                        
                        await send_ws_message(
                        websocket=self.websocket,
                        type="aura_context_tool_request",
                        task_id=self.task_id,
                        chat_id=self.chat_id,
                        payload={
                            "tool": tool_name,
                            "input": tool_input,
                        }
                    )
            
                # === TOOL RESULT PHASE ===
                elif last_message.type == "tool":
                    print(f"✅ Result: {last_message.content}")

                    tool_call_id = last_message.tool_call_id
                    tool_info = active_tool_calls.get(tool_call_id, {})
                    tool_name = tool_info.get("tool_name", "unknown")

                    await send_ws_message(
                        websocket=self.websocket,
                        type="aura_context_tool_response",
                        task_id=self.task_id,
                        chat_id=self.chat_id,
                        payload={
                            "tool": tool_name,
                            "content":{
                                "role": "tool",
                                "output": last_message.content,
                            }
                        }
                    )
            
                # === FINAL RESPONSE PHASE ===
                elif last_message.type == "ai" and last_message.content:
                    print(f"💬 Response: {last_message.content}")
                    full_response = last_message.content
                    
                    await send_ws_message(
                        websocket=self.websocket,
                        type="aura_context_message",
                        task_id=self.task_id,
                        chat_id=self.chat_id,
                        payload={
                            "content": {
                                "role": "assistant",
                                "message": last_message.content,
                            }
                        }
                    )
            
            # creating the agent event to store the response in db
            await create_agent_event(
                pool=self.dbpool,
                task_id=self.task_id,
                role="assistant",
                message_type="aura_context_message",
                tool="foreground_app",
                payload={
                    "content": {
                        "message": full_response
                    },
                },
            )

            # updating the task status to completed
            await update_task_status(self.dbpool, self.task_id, "completed")

            return full_response

        except Exception as e:
            raise RuntimeError(f"Error running BROWSER agent: {str(e)}")
            return None