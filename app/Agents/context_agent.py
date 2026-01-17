from fastapi import WebSocket
from langchain_core.language_models.chat_models import BaseChatModel
from app.Prompts.ide_app_prompt import IDE_AGENT_PROMPT
from langchain_core.messages import HumanMessage, SystemMessage
from langchain.agents import create_agent
from asyncpg import Pool
from typing import Optional

from app.LLM.memory import Memory
from app.Types.context_type import IDEContextState
from app.api.websocket_utils import send_ws_message
from app.DB.Queries.agent_event import create_agent_event
from app.DB.Queries.task import update_task_status

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
            result = await self.run_ide_agent()
            
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

            agent = create_agent(
                model=self.llm,
                tools=[],
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