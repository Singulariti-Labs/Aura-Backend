from app.LLM.llm_factory import LLMFactory
from langchain_core.output_parsers import JsonOutputParser
from langchain.chat_models import ChatOpenAI, ChatAnthropic
from langchain.agents import create_openai_tools_agent, create_tool_calling_agent, AgentExecutor
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables import Runnable
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder

from app.Prompts.planner import PLANNER_PROMPT
from app.Agents.base_agent import BaseAgent

class PlannerAgent(BaseAgent) :
    """
        Planner agent takes the query and provide a structured plan using COT if query is complex if 
        query is simple and task can be done using single available agent then provide proper description of the task
    """

    def __init__(self):
        self.planner_prompt = PLANNER_PROMPT
        self.router_parser = JsonOutputParser
    
    def create_agent(self, llm: BaseChatModel):  #can be keep in baseclass

        prompt = ChatPromptTemplate.from_messages([
            ("system", self.planner_prompt),
            MessagesPlaceholder(variable_name="chat_history"),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])

        if isinstance(llm, ChatOpenAI):
            return create_openai_tools_agent(llm, self.tools, prompt)
        elif isinstance(llm, ChatAnthropic):
            return create_tool_calling_agent(llm, self.tools, prompt)
        else:
            raise ValueError(f"Unsupported LLM type: {type(llm)}")
    
    async def execute(self, llm: BaseChatModel, agent: Runnable, query: str) -> AgentExecutor:
        agent_executor = AgentExecutor(
            agent=agent,
            tools=self.tools,
            verbose=True,
            return_intermediate_steps=True
        )
        result = agent_executor.invoke({"input": query})
        return result

    async def run(self, llm: BaseChatModel, query: str):
        """Determine if the query is complex and which agent should handle it"""
        
        agent = self.create_agent(llm)
        response = await self.execute(agent, llm, agent, query)
        print(f"{response}")
        return response



        