from fastapi import WebSocket
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain.agents import create_agent
from asyncpg import Pool
from typing import Optional, Dict, Any
from langchain_core.runnables import Runnable
from app.LLM.memory import Memory
from datetime import datetime, timezone


from app.Types.context_type import IDEContextState
from app.api.websocket_utils import send_ws_message
from app.DB.Queries.agent_event import create_agent_event
from app.DB.Queries.task import update_task_status
from app.Prompts.ide_app_prompt import IDE_AGENT_PROMPT
from app.Prompts.browser_page_option import BROWSER_APP_PROMPT
from app.Prompts.general_agent_prompt import GENERAL_AGENT_PROMPT
from app.Tools.Foreground_App_Tools.web_search_tool import web_search_tool
from app.Tools.Foreground_App_Tools.web_scraping_tool import web_scraping_tool
from app.Task.task_manager import task_manager



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
        self.task_state = task_manager.get_state(self.task_id)

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
            print("QUERY: ", self.query)

            tools = [web_search_tool]

            agent = create_agent(
                model=self.llm,
                tools=tools,
                system_prompt=formatted_system_prompt
            )

            inputs = {
                "messages": [
                    ("user", self.query)
                ]
            }

            full_response = await self.stream_agent_response(agent, inputs)

            print(f"\n\n✅ FINAL RESPONSE: {full_response}")
            
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
                seq=self.task_state.get_next_seq()
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

            inputs = {
                "messages": [
                    ("user", self.query)
                ]
            }

            full_response = await self.stream_agent_response(agent, inputs)
            
            print(f"\n\n✅ FINAL RESPONSE: {full_response}")

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
                seq=self.task_state.get_next_seq()
            )

            # updating the task status to completed
            await update_task_status(self.dbpool, self.task_id, "completed")

            return full_response

        except Exception as e:
            raise RuntimeError(f"Error running BROWSER agent: {str(e)}")
            return None

    async def stream_agent_response(self, agent: Runnable, inputs: Dict[str, Any] ):
        """Streams the Context-Agent-Response to the client with tool call and response events
        
        Streams Events ->
            [on_tool_start]: TOOL_NAME + TOOL_INPUT
            [on_tool_end]: TOOL_NAME + TOOL_RESPONSE

            [on_chat_model_stream]: Stream Response Token By Token.
        
        Returns -> 
            [current_response]: Full Response from the agent.
        """ 

        current_response = ""

        async for event in agent.astream_events(inputs, version="v2"):
            event_type = event["event"]

            # 1. TOOL CALL START
            if event_type == "on_tool_start":
                tool_name = event["name"]
                tool_input = event["data"].get("input")

                await send_ws_message(
                    websocket=self.websocket,
                    type="aura_context_tool_request",
                    task_id=self.task_id,
                    chat_id=self.chat_id,
                    payload={
                        "tool": tool_name,
                        "input": tool_input,
                    },
                )

                # creating the agent event to store the tool request in db
                await create_agent_event(
                    pool=self.dbpool,
                    task_id=self.task_id,
                    role="assistant",
                    message_type="aura_context_tool_request",
                    tool=tool_name,
                    payload={
                        "tool": tool_name,
                        "input": tool_input,
                    },
                    seq=self.task_state.get_next_seq()
                )

                print(f"\n\n✅ TOOL REQUEST: {tool_name}")
                print(f"\n\n✅ TOOL INPUT: {tool_input}")

            # 2. TOOL CALL END
            elif event_type == "on_tool_end":
                tool_name = event["name"]
                tool_output = event["data"].get("output")

                print(f"\n\n✅ TOOL NAME: {tool_name}")
                print(f"\n\n✅ TOOL RESPONSE: {tool_output.content}")

                await send_ws_message(
                    websocket=self.websocket,
                    type="aura_context_tool_response",
                    task_id=self.task_id,
                    chat_id=self.chat_id,
                    payload={
                        "tool": tool_name,
                        "content": {
                            "role": "tool",
                            "output": tool_output.content,
                        },
                    },
                )

                # creating the agent event to store the tool response in db
                await create_agent_event(
                    pool=self.dbpool,
                    task_id=self.task_id,
                    role="tool",
                    message_type="aura_context_tool_response",
                    tool=tool_name,
                    payload={
                        "tool": tool_name,
                        "content": {
                            "role": "tool",
                            "output": tool_output.content,
                        },
                    },
                    seq=self.task_state.get_next_seq()
                )

            # 3. ASSISTANT TOKEN STREAM
            elif event_type == "on_chat_model_stream":
                chunk = event["data"]["chunk"]

                if chunk.content:
                    # Handle both string (OpenAI) and list (Gemini) content
                    if isinstance(chunk.content, list):
                        # Gemini returns a list of content blocks
                        content_text = "".join([
                            block.get("text", "") if isinstance(block, dict) else str(block)
                            for block in chunk.content
                        ])
                    else:
                        # OpenAI returns a string
                        content_text = chunk.content
                    
                    current_response += content_text

                    await send_ws_message(
                        websocket=self.websocket,
                        type="aura_context_message",
                        task_id=self.task_id,
                        chat_id=self.chat_id,
                        payload={
                            "content": {
                                "role": "assistant",
                                "message": content_text       # delta
                            }
                        },
                    )

        return current_response

    async def run_general_agent(self):
        """
        Run the general agent to give answer to the user query realted to general questions.
        """
        try:
            current_date = datetime.now().strftime("%Y-%m-%d")

            formatted_system_prompt = GENERAL_AGENT_PROMPT.format(
                today= current_date
            )

            tools = [web_search_tool, web_scraping_tool]

            agent = create_agent(
                model=self.llm,
                tools=tools,
                system_prompt=formatted_system_prompt
            )

            inputs = {
                "messages": [
                    ("user", self.query)
                ]
            }

            full_response = await self.stream_agent_response(agent, inputs)
            
            print(f"\n\n✅ FINAL RESPONSE: {full_response}")

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
                seq=self.task_state.get_next_seq()
            )

            # updating the task status to completed
            await update_task_status(self.dbpool, self.task_id, "completed")

            return full_response
            
        except Exception as e:
            raise RuntimeError(f"Error running general agent: {str(e)}")
            return None