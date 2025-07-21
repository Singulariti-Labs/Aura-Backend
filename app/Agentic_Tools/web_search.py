from typing import Optional
from langchain_tavily import TavilySearch
import json

from app.LLM.memory import Memory
from app.helper import update_memory


async def web_search(query: str, num_results: Optional[int] = 20, memory: Optional[Memory] = None, tool_call_id: Optional[str] = None):
    """
    This is the web search tool which takes the query, max_results and returns the results.

    input:
        query: str
        num_results: Optional[int] = 20
        memory: Optional[Memory] = None
        tool_call_id: Optional[str] = None

    output:
        result: list[dict]
    """
    try:
        search_tool = TavilySearch(max_results= num_results, topic= "general")    

        result = search_tool.invoke({"query": query})
        
        # Update local memory with the conversation
        update_memory(role="user", content=query, memory=memory)
        update_memory(role="tool", name="web_search", tool_call_id=tool_call_id, content=json.dumps(result), memory=memory)
        
       # WIP - sending the websocket message to client.

        return result
    except Exception as e:
        return f"Error in web search for query: {query}\n error: {e}"


