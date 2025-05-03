from app.LLM.llm_factory import LLMFactory
from langchain_core.output_parsers import JsonOutputParser
from langchain_openai.chat_models.base import ChatOpenAI
from langchain_community.chat_models.anthropic import ChatAnthropic
from langchain.agents import create_openai_tools_agent, create_tool_calling_agent, AgentExecutor
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables import Runnable
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder
from typing import Optional

from app.Prompts.planner import PLANNER_PROMPT
from app.Agents.base_agent import BaseAgent
from app.LLM.memory import Memory

class PlannerAgent(BaseAgent) :
    """
    The PlannerAgent is responsible for analyzing a user query and deciding how to approach the task:

        - If the query is **simple**, it returns a brief structured description indicating how the task can be completed directly by a suitable
          agent.
        - If the query is **complex**, it generates a **step-by-step plan** (using Chain-of-Thought reasoning) outlining how the task should be
          divided and which tools or sub-agents are required to accomplish each step.

    This makes it ideal as a high-level controller in a multi-agent system where task decomposition is essential.
    """

    def __init__(self, memory: Optional[Memory] = None):
        self.planner_prompt = PLANNER_PROMPT
        self.memory = memory
        self.llm_factory = LLMFactory(self.memory)

    async def run(self, llm: BaseChatModel, query: str):
        """
        Executes the planner logic by passing the query to the appropriate LLM pipeline.

        Input:
            llm (BaseChatModel): An instance of the language model (e.g., OpenAI or Anthropic).
            query (str): The user’s question or task description.

        Returns:
            Any: The output from the LLM—either a structured plan or a single-step task description.

        Raises:
            RuntimeError: If any error occurs during planning or while calling the LLM.
        """
        try: 
            response = await self.llm_factory.invoke_planner_agent(llm=llm, prompt_template=self.planner_prompt, query=query)
            print(f"{response}")
            return response
        except Exception as e:
            raise RuntimeError(f"Error while running Planner agent ERROR: {e}")