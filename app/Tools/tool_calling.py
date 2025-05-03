from langchain_core.language_models.chat_models import BaseChatModel

from app.LLM.memory import Memory
from app.Tools.supervisor import SupervisorTool

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
        self.supervisor_agent = SupervisorTool(llm=self.llm, memory=self.memory)

    
    def get_agent_tools(self):
        """
        Returns a list of initialized tools formatted as LangChain-compatible Tool instances.
        It provides all the Tools that can be accessed by main agent

        Return: List[Tool] (Tool->langchain tool)
        """

        tools = [
            self.supervisor_agent.to_tool()
        ]
        return tools