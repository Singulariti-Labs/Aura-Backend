import asyncio

from app.Agents.supervisor import SupervisorAgent
from app.Agents.agent import Agent
from app.Agents.planner import PlannerAgent
from app.Types.agent_types import LLMConfig, SystemInfo

llm_config = LLMConfig(provider="openai", model_name="gpt-4o")
system_info = SystemInfo(os="windows", version="11")  # replace with actual init

query = "What is the current conditions of the Gold is it worth to invest in Gold now?"

async def main():
    llm=llm_config
    agent = Agent(llm=llm, query=query, system_info=system_info)
    response = await agent.invoke()

asyncio.run(main())