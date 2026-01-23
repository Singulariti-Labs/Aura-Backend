import json
import asyncio
from typing import List, Optional
from langchain.tools import tool
from langchain_tavily import TavilySearch
from app.api.websocket_utils import send_ws_message
from app.Task.task_manager import task_manager
from app.DB.Queries.agent_event import create_agent_event


def _extract_confidence(item: dict) -> float:
    return (
        item.get("score")
        or 0.0
    )


async def _run_single_search(search_tool: TavilySearch, query: str):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: search_tool.invoke({"query": query})
    )


@tool("web_search_tool")
async def web_search_tool(
    queries: List[str],
    num_results: Optional[int] = 20,
):
    """
    This web search tool is used by the AURA Context Agent and the Foreground App Agent
    to retrieve up-to-date information from the internet.

    It accepts one or more search queries, runs all searches concurrently, and combines
    the results into a single ranked list. The combined results are globally sorted by
    relevance/confidence, and only the top 20 most relevant matches are returned.

    Use this tool when answering user queries that require current, factual, or
    externally sourced information that cannot be reliably answered from internal
    context alone.
    """

    try:

        search_tool = TavilySearch(
            max_results=num_results,
            topic="general"
        )

        # Run all queries concurrently
        search_tasks = [
            _run_single_search(search_tool, query)
            for query in queries
        ]

        raw_results = await asyncio.gather(*search_tasks)

        # Flatten
        combined_results = []
        for result in raw_results:
            if isinstance(result, dict) and "results" in result:
                combined_results.extend(result["results"])
            elif isinstance(result, list):
                combined_results.extend(result)

        # Sort by score
        combined_results.sort(
            key=lambda x: x.get("score", 0.0),
            reverse=True
        )

        final_results = combined_results[:20]

        response = {
            "status": "success",
            "results": final_results
        }
        
        return response

    except Exception as e:
        response = {
            "status": "failed",
            "error": str(e)
        }
        return response