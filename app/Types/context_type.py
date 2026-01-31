from typing_extensions import NotRequired
from langchain.agents import AgentState

class IDEContextState(AgentState):
    file_name: NotRequired[str]
    file_content: NotRequired[str]
    app_name: NotRequired[str]
    app_type: NotRequired[str]