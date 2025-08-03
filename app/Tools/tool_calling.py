from typing import TYPE_CHECKING
from langchain_core.language_models.chat_models import BaseChatModel

from app.LLM.memory import Memory
from app.Tools.supervisor import SupervisorTool
from app.Tools.interaction import InteractionTool
from app.Tools.deep_research import DeepResearchTool
from app.Tools.web_search import WebSearchTool
from app.Tools.web_scraper import WebScraperTool
from app.Tools.ask import AskTool
from app.Tools.complete import CompleteTool

if TYPE_CHECKING:
    from app.Agents.supervisor import SupervisorAgent

class Tools():
    """
    This class encapsulates tool setup logic and exposes them in a format compatible
    with LangChain's tool interface.
    """
    def __init__(self, llm: BaseChatModel, memory: Memory, task_id: str, chat_id: str):
        """
        Initializes the Tools manager with an LLM and memory.

        Input:
        - llm: A language model instance that tools will use for reasoning and output generation.
        - memory: A memory object to store contextual conversation history or state.
        - task_id: A unique identifier for a task.
        - chat_id: A unique identifier for a chat.
        Sets up individual tools like the SupervisorTool internally.
        """
        self.memory = memory
        self.llm = llm
        self.task_id = task_id
        self.chat_id = chat_id

         # Import at runtime to break the cycle
        from app.Agents.supervisor import SupervisorAgent
        self.supervisor_agent: "SupervisorAgent" = SupervisorAgent(llm=self.llm, memory=self.memory, tools=self, task_id=self.task_id, chat_id=self.chat_id)

        self.supervisor_tool = SupervisorTool(supervisor_agent=self.supervisor_agent, llm=self.llm, memory=self.memory, task_id=self.task_id, chat_id=self.chat_id)
        self.interaction_tool = InteractionTool(llm=self.llm, memory=self.memory, task_id=self.task_id, chat_id=self.chat_id)
        self.deep_research_tool = DeepResearchTool(llm=self.llm, memory=self.memory, task_id=self.task_id, chat_id=self.chat_id)
        self.web_search_tool = WebSearchTool(memory=self.memory, task_id=self.task_id, chat_id=self.chat_id)
        self.web_scraping_tool = WebScraperTool(memory=self.memory, task_id=self.task_id, chat_id=self.chat_id)
        self.ask_tool = AskTool(memory=self.memory, task_id=self.task_id, chat_id=self.chat_id)
        self.complete_tool = CompleteTool(memory=self.memory, task_id=self.task_id, chat_id=self.chat_id)

    
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
        """
        Returns a list of initialized tools formatted as LangChain-compatible Tool instances.
        It provides all the Tools that can be accessed by superviosr agent

        Return: List[Tool] (Tool->langchain tool)
        """
        tools = [self.interaction_tool.to_tool(),
                 self.deep_research_tool.to_tool(),
                 self.web_search_tool.to_tool(),
                 self.web_scraping_tool.to_tool(),
                 self.ask_tool.to_tool(),
                 self.complete_tool.to_tool()
                ]
        return tools