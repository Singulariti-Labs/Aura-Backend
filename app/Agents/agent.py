from typing import Optional

from app.Agents.base_agent import BaseAgent
from app.Types.agent_types import LLMConfig, SystemInfo
from app.LLM.llm_factory import LLMFactory
from app.LLM.memory import Message, Memory
from app.Prompts.agent import AGENT_PROMPT
from app.Tools.tool_calling import Tools


class Agent(BaseAgent):
    """ 
    This is the main agent that will do the normal connversation and will decide wether to call the supervisor agent or not on the 
    basis of the query. If user wants to perform any task rather than normal search or conversation then it calls the supervidor agent.
    """

    def __init__(
        self,
        query: str,
        system_info: SystemInfo,
        llm: LLMConfig,
        maxTokens: int = 128000,
        screenshot: Optional[str] = None,
    ):
        self.query = query
        self.llm_config = llm
        self.memory = Memory()
        self.llm_factory = LLMFactory(self.memory)
        self.llm = LLMFactory.create_llm(llm)
        self.max_tokens = maxTokens
        self.system_info = None
        self.screenshot = screenshot
        self.agent_prompt = AGENT_PROMPT
        self.tools = Tools(llm=self.llm, memory=self.memory)
        self.max_tokens = maxTokens
        self.system_info = system_info
        

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
            user_message = Message.user_message(content=self.query, base64_image=self.screenshot)
            self.memory.add_message(user_message)

            chat_history = self.memory.messages

            try:
                available_tools = self.tools.get_agent_tools()

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
        


        

        
      



