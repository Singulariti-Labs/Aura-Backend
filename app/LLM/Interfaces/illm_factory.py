from typing import Optional, List, Union, Dict, Any
from langchain_core.language_models.chat_models import BaseChatModel
from langchain.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from langchain.tools import Tool

from app.Types.agent_types import LLMConfig, SystemInfo
from app.LLM.memory import Message

class ILLMFactory():
    """
    LLMFactory handles creation and execution of language model agents with optional tools, multimodal inputs, 
    and memory support for chat history.
    """

    @staticmethod
    def create_llm(llm_config: LLMConfig):
        """
        Creates a language model instance based on the given provider and model name.

        Input:
        - llm_config: Configuration containing provider and model_name.

        Returns:
        - An instance of ChatOpenAI or ChatAnthropic.
        """
    
    @staticmethod
    def get_agent_type(llm: BaseChatModel, prompt: ChatPromptTemplate, tools: Optional[List[Tool]] = None):
        """
        Determines the appropriate agent creation method based on the type of LLM.

        Input:
        - llm: A BaseChatModel instance.
        - prompt: A ChatPromptTemplate to guide the agent's behavior.
        - tools: Optional list of tools to be used by the agent.

        Returns:
        - An agent configured with the provided tools and LLM.
        """
    
    @staticmethod
    def invoke_agent(
        llm:BaseChatModel,
        agent: Runnable,
        tools: List[Tool],
        system_info: SystemInfo,
        query: Union[str, List[Dict[str, Union[str, dict]]]],
        chat_history: Optional[List[Message]] = [],
    ):
        """
        Invokes an agent with the given input, tools, and memory context.

        Input:
        - llm: The language model used by the agent.
        - agent: The runnable agent instance.
        - tools: List of tools to assist the agent.
        - system_info: Optional system-level context.
        - query: The user input (text or multimodal).
        - chat_history: Optional list of previous messages.

        Returns:
        - The final response from the agent after execution.
        """
    
    def get_multimodal_query(query: str, screenshot: str):
        """
        Formats a multimodal query combining user text and screenshot data.

        Input:
        - query: User's textual input.
        - screenshot: A base64 image string or URL.

        Returns:
        - A list combining text and image input formatted for multimodal LLMs.
        """
    
    async def agent_executor( #WIP
        self,
        system_prompt: str,
        llm: BaseChatModel,
        query: str,
        system_info: Optional[SystemInfo] = None,
        tools: Optional[List[Tool]] = None,
        # chat_history: Optional[List[Union[HumanMessage, AIMessage, SystemMessage]]] = None,
        chat_history: Optional[List[Message]] = None,
        screenshot: Optional[str] = None,
        max_tokens: int = 128000,
    ):
        """
        Executes a user query using the agent or directly via LLM, with optional tools, chat history, and image input.

        Input:
        - system_prompt: Initial system prompt to guide the agent.
        - llm: The language model to use.
        - query: The user's question or command.
        - system_info: Optional system-specific information.
        - tools: Optional list of tools the agent can use.
        - chat_history: Optional list of previous messages to maintain continuity.
        - screenshot: Optional image input in base64 or URL.
        - max_tokens: Maximum token limit for the LLM response.

        Returns:
        - The response from the agent or LLM after processing the query.
        """
    
    async def invoke_planner_agent(llm: BaseChatModel, prompt_template: str, query: str) -> List[Dict[str, str]]:
        """
        Uses a language model to generate a structured multi-step plan from a user query.

        Input:
        - llm: The language model instance to generate the plan.
        - prompt_template: Template string with placeholders for query and formatting.
        - query: The complex input question to be broken down.

        Returns:
        - A list of steps, each with id, description, thought, dependency, and expected output.
        """