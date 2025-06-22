from typing import Optional
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import Tool as LangchainTool
import uuid

from app.Tools.base_tool import BaseTool
from app.Tools.tool_input_parser import ToolInputParser
from app.Agents.deep_research import DeepResearchAgent
from app.LLM.memory import Memory
from app.Types.agent_types import DeepResearchToolInput

class DeepResearchTool(BaseTool):
    def __init__(self, llm: BaseChatModel, task_id: str, memory: Optional[Memory] = None):
        """
        Initialize DeepResearchTool with a language model and memory

        Input:
        - llm: A chat-based LLM instance that powers the SupervisorAgent.
        - memory: Optional memory object to retain conversation history/context.
        """

        super().__init__(
            name="supervisor",
            description="Plans and delegates tasks to sub-agents.",
            memory=memory,
            args_schema=DeepResearchToolInput
        )
        self.deep_research_agent = DeepResearchAgent(llm=llm, task_id=task_id, memory=memory)

    async def run(self, inputs: DeepResearchToolInput) -> str:

        query = inputs.query
        base64_image = inputs.base64_image
        tool_call_id = str(uuid.uuid4())
        response = await self.deep_research_agent.invoke(
            query=query,
            base64_image=base64_image,
            tool_call_id=tool_call_id
        )
        return response
        