from typing import Optional, List

from app.Agents.base_agent import BaseAgent
from app.Types.agent_types import LLMConfig, SystemInfo
from app.LLM.llm_factory import LLMFactory
from app.LLM.memory import Message, Memory
from app.Prompts.agent import AGENT_PROMPT
from app.Tools.tool_calling import Tools
from app.Task.task_manager import task_manager
from app.api.websocket_utils import send_ws_message

import asyncio


class Agent(BaseAgent):
    """ 
    This is the main agent that will do the normal connversation and will decide wether to call the supervisor agent or not on the 
    basis of the query. If user wants to perform any task rather than normal search or conversation then it calls the supervidor agent.
    """

    def __init__(
        self,
        query: str,
        task_id: str,
        chat_id: str,
        system_info: SystemInfo,
        llm: LLMConfig,
        maxTokens: int = 128000,
        screenshot:  Optional[List[str]] = None
    ):
        self.query = query
        self.task_id = task_id
        self.chat_id = chat_id
        self.llm_config = llm
        self.memory = Memory()
        self.llm_factory = LLMFactory(self.memory)
        self.llm = LLMFactory.create_llm(llm)
        self.max_tokens = maxTokens
        self.system_info = None
        self.screenshot = screenshot
        self.agent_prompt = AGENT_PROMPT
        self.tools = Tools(llm=self.llm, memory=self.memory, task_id=self.task_id, chat_id=self.chat_id)
        self.max_tokens = maxTokens
        self.system_info = system_info
        # self.task_manager = TaskManager()
        

    async def invoke(self):
        """
        Executes the main agent by sending a user query and optional screenshot to the LLM with the configured tools.

        This method performs the following:
        - Constructs a user message from the query and optional screenshot.
        - Stores the message in memory to maintain chat history.
        - Retrieves available tools for the agent.
        - Calls the LLM agent executor with the query, chat history, tools, system prompt, and system info.
        - Returns the result produced by the agent.

        Raises:
            RuntimeError: If an error occurs while invoking the agent or creating the LLM instance.
        """

        try:
            # Get web socket from task manager
            task_state = task_manager.get_state(self.task_id)
            self.websocket = task_state.websocket

            # Notify client present inside Main Agent
            await send_ws_message(
                websocket=self.websocket,
                type="aura_status",
                task_id=self.task_id,
                chat_id=self.chat_id,
                payload={
                    "query": self.query,
                    "message": "Running <AURA>",
                    "status": "processing",
                }
            )

            user_message = Message.user_message(content=self.query, base64_images=self.screenshot)
            self.memory.add_message(user_message)

            chat_history = self.memory.messages

            try:
                # ⏸ Pause check before any heavy work
                await task_manager.wait_if_paused(self.task_id)

                available_tools = self.tools.get_agent_tools()

                # ❌ Optional cancel check (recommended)
                if task_manager.get_state(self.task_id).cancelled:
                    raise asyncio.CancelledError()
                
                # ⏸ Pause check again before the LLM call
                await task_manager.wait_if_paused(self.task_id)

                result = await self.llm_factory.agent_executor(
                    llm=self.llm,
                    query=self.query,
                    screenshot=self.screenshot,
                    system_prompt=self.agent_prompt,
                    chat_history=chat_history,
                    tools=available_tools,
                    system_info=self.system_info,
                    agent_type="main"
                )

                # SEND_RESPONSE_TO_CLIENT - Agent output

                print(f"RESULT OF AGENT: {result}")
                return result
            
            except Exception as e:
                raise RuntimeError(
                    f"Error while calling Agent: Error -> {str(e)}"
                )

        except Exception as e:
            raise RuntimeError(
                f"Error creating LLM instance for provider '{self.llm_config.provider}' "
                f"with model '{self.llm_config.model_name}': {str(e)}"
            )
