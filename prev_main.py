import asyncio

from app.Agents.supervisor import SupervisorAgent
from app.Agents.agent import Agent
from app.Agents.planner import PlannerAgent
from app.Types.agent_types import LLMConfig, SystemInfo

llm_config = LLMConfig(provider="openai", model_name="gpt-4o")
system_info = SystemInfo(os="windows", version="11")  # replace with actual init
query = "give a breif analysis on tesla stock?" # will get from client when connected to websocket

async def main():
    llm=llm_config
    agent = Agent(llm=llm, system_info=system_info)
    response = await agent.invoke(query)
    print(response)

asyncio.run(main())