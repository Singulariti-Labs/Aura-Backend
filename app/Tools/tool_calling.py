from typing import TYPE_CHECKING
from langchain_core.language_models.chat_models import BaseChatModel

from app.LLM.memory import Memory
from app.Tools.supervisor import SupervisorTool
from app.Tools.interaction import InteractionTool
from app.Tools.deep_research import DeepResearchTool

if TYPE_CHECKING:
    from app.Agents.supervisor import SupervisorAgent

class Tools():
    """
    This class encapsulates tool setup logic and exposes them in a format compatible
    with LangChain's tool interface.
    """
    def __init__(self, llm: BaseChatModel, memory: Memory):
        """
        Initializes the Tools manager with an LLM and memory.

        Input:
        - llm: A language model instance that tools will use for reasoning and output generation.
        - memory: A memory object to store contextual conversation history or state.

        Sets up individual tools like the SupervisorTool internally.
        """
        self.memory = memory
        self.llm = llm

         # Import at runtime to break the cycle
        from app.Agents.supervisor import SupervisorAgent
        self.supervisor_agent: "SupervisorAgent" = SupervisorAgent(llm=self.llm, memory=self.memory, tools=self)

        self.supervisor_tool = SupervisorTool(supervisor_agent=self.supervisor_agent, llm=self.llm, memory=self.memory)
        self.interaction_tool = InteractionTool(llm=self.llm, memory=self.memory)
        self.deep_research_tool = DeepResearchTool(llm=self.llm, memory=self.memory)

    
    def get_agent_tools(self):
        """
        Returns a list of initialized tools formatted as LangChain-compatible Tool instances.
        It provides all the Tools that can be accessed by main agent

        Return: List[Tool] (Tool->langchain tool)
        """

        tools = [
            self.supervisor_tool.to_tool()
        ]
        return tools
    
    def get_supervisor_tools(self):

        tools = [self.interaction_tool.to_tool(), self.deep_research_tool.to_tool()]
        return tools