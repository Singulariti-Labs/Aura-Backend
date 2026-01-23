from typing import Optional, List, Any, Dict
from fastapi import WebSocket
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from asyncpg import Pool


from app.Agents.base_agent import BaseAgent
from app.Types.agent_types import LLMConfig, SystemInfo
from app.LLM.llm_factory import LLMFactory
from app.LLM.memory import Message, Memory
from app.Prompts.agent import AGENT_PROMPT
from app.Prompts.classifier_prompt import CLASSIFIER_PROMPT
from app.Prompts.browser_page_option import BROWSER_APP_PROMPT
from app.Prompts.recall_memory import RECALL_MEMORY_PROMPT
from app.Tools.tool_calling import Tools
from app.Task.task_manager import task_manager
from app.api.websocket_utils import send_ws_message
from app.Option_Helper.web_scraper import simple_web_scraper
from app.DB.Queries.agent_event import create_agent_event
from app.Agents.context_agent import ContextAgent

import asyncio
import json
import requests
import os
from dotenv import load_dotenv

load_dotenv()


class Agent(BaseAgent):
    """ 
    This is the main agent that will do the normal connversation and will decide wether to call the supervisor agent or not on the 
    basis of the query. If user wants to perform any task rather than normal search or conversation then it calls the supervidor agent.
    """

    def __init__(
        self,
        query: str,
        payload: dict,
        task_id: str,
        chat_id: str,
        system_info: SystemInfo,
        llm: LLMConfig,
        maxTokens: int = 128000,
        screenshot:  Optional[List[str]] = None,
        pool: Pool | None = None
    ):
        self.query = query
        self.task_id = task_id
        self.chat_id = chat_id
        self.dbpool = pool
        self.llm_config = llm
        self.llm_provider = llm.provider
        self.memory = Memory()
        self.llm_factory = LLMFactory(self.memory)
        self.llm = LLMFactory.create_llm(llm)
        self.max_tokens = maxTokens
        self.system_info = None
        self.screenshot = screenshot
        self.agent_prompt = AGENT_PROMPT
        self.tools = Tools(llm=self.llm, memory=self.memory, task_id=self.task_id, chat_id=self.chat_id)
        self.max_tokens = maxTokens
        self.system_info = system_info
        self.payload = payload
        # self.task_manager = TaskManager()
        
    # Runs the Aura Agent.
    async def invoke(self):
        """
        Executes the Aura Agent by sending a user query and optional screenshot to the LLM with the configured tools.

        This method performs the following:
        - Constructs a user message from the query and optional screenshot.
        - Stores the message in memory to maintain chat history.
        - Retrieves available tools for the agent.
        - Calls the LLM agent executor with the query, chat history, tools, system prompt, and system info.
        - Returns the result produced by the agent.

        Raises:
            RuntimeError: If an error occurs while invoking the agent or creating the LLM instance.
        """

        try:
            # Get web socket from task manager
            task_state = task_manager.get_state(self.task_id)
            self.websocket = task_state.websocket

            # Notify client present inside Main Agent
            await send_ws_message(
                websocket=self.websocket,
                type="aura_status",
                task_id=self.task_id,
                chat_id=self.chat_id,
                payload={
                    "query": self.query,
                    "message": "Running <AURA>",
                    "status": "processing",
                }
            )

            user_message = Message.user_message(content=self.query, base64_images=self.screenshot)
            self.memory.add_message(user_message)

            chat_history = self.memory.messages


            try:
                # ⏸ Pause check before any heavy work
                await task_manager.wait_if_paused(self.task_id)

                available_tools = self.tools.get_agent_tools()

                # ❌ Optional cancel check (recommended)
                if task_manager.get_state(self.task_id).cancelled:
                    raise asyncio.CancelledError()
                
                # ⏸ Pause check again before the LLM call
                await task_manager.wait_if_paused(self.task_id)
                result = None

                # If the option is not complex_task or smart then run the main agent
                if self.payload.get('option') not in ["complex_task", "smart"]:
                    context_agent = ContextAgent(
                        query=self.query,
                        payload=self.payload,
                        task_id=self.task_id,
                        chat_id=self.chat_id,
                        llm=self.llm,
                        screenshot=self.screenshot,
                        websocket=self.websocket,
                        llm_provider=self.llm_provider,
                        memory= self.memory,
                        dbpool=self.dbpool
                    )

                if self.payload.get("option") == "browser_page":
                    print("BROWSER_PAGE: Running Browser Page Agent")
                    result = await self.run_browser_page_agent(
                        websocket=self.websocket,
                        query=self.query,
                        payload = self.payload,
                        screenshot=self.screenshot,
                        llm=self.llm,
                        task_id=self.task_id,
                        chat_id=self.chat_id
                    )

                elif self.payload.get("option") == "history":
                    print("SEARCH HISTORY: Running Recall Memeory Agent")
                    result = await self.run_recall_memory_agent()
                
                elif self.payload.get("option") == "foreground_app":
                    print("FOREGROUND APP: Running Foreground App Agent")
                    result = await context_agent.run_foreground_app_agent()

                else:
                    result = await self.llm_factory.agent_executor(
                        llm=self.llm,
                        query=self.query,
                        screenshot=self.screenshot,
                        system_prompt=self.agent_prompt,
                        chat_history=chat_history,
                        tools=available_tools,
                        system_info=self.system_info,
                        llm_provider=self.llm_provider,
                        agent_type="main"
                    )

                # SEND_RESPONSE_TO_CLIENT - Agent output
                print(f"\n\n----- AGENT RUN FINISHED -----\n\n")
                return result
            
            except Exception as e:
                raise RuntimeError(
                    f"Error while calling Agent: Error -> {str(e)}"
                )

        except Exception as e:
            raise RuntimeError(
                f"Error creating LLM instance for provider '{self.llm_config.provider}' "
                f"with model '{self.llm_config.model_name}': {str(e)}"
            )

# ------------------------------------------------------------------------- #
# ---------------  Functions To run Browser Agent ------------------------- #
# ------------------------------------------------------------------------- #

    # TODO: NO need to send parameters which are initialised.
    async def run_browser_page_agent(self, websocket: WebSocket, query: str, payload: dict, screenshot: List[str], llm: BaseChatModel, task_id: str, chat_id: str):
        """ It is the function only runs when the option is browser_page and it will tell about any page on the browser by reading url and 
        featching its content. If the page is not a web page ie is_browser=false then will reply as per the app_info from payload."""
        try:
            is_browser = payload.get("app_details", {}).get("is_browser")
            print("BROWSER_PAGE: inside browser page agent")
            if is_browser:
                url = payload.get("app_details", {}).get("url")
                web_scraper_response = await simple_web_scraper(url)
                results = web_scraper_response.get("results") or []
                page_content = results[0].get("content") if results else ""
                # page_content = web_scraper_response["results"][0]["content"]  commited due to error in the last shunk of message.
                page_title = payload.get("app_details", {}).get("page_title", "Untitled")

                context = (
                    f"Page Title: {page_title}\n"
                    f"Page Content: {page_content}"
                )

                decision = self.decide_source(llm, query, context)
                source = decision.get("source")

                if source == "page":
                    answer = decision.get("answer", "No answer returned.")
                    updated_context = f"Page Context:\n{context}\n\nSuggested Answer:\n{answer}"
                    print("BROWSER_PAGE: Streaming Page Response")
                    await self.stream_answer(
                        llm=llm,
                        query=query,
                        context=updated_context,
                        websocket=websocket,
                        task_id=task_id,
                        chat_id=chat_id,
                        tool="browser_page",
                    )
                    return

                if source == "search":
                    print("BROWSER_PAGE: 🔎 Page content insufficient. Fetching Tavily results...")
                    tavily_results = self.fetch_tavily_results(query)
                    context = f"Tavily Search Results:\n{self.format_search_results(tavily_results)}"
                    await self.stream_answer(
                        llm=llm,
                        query=query,
                        context=context,
                        websocket=websocket,
                        task_id=task_id,
                        chat_id=chat_id,
                        tool="browser_page",
                    )
                    return
            else:
                app_name = payload.get("app_details", {}).get("app", "Unknown App")
                page_title = payload.get("app_details", {}).get("page_title", "Untitled")

                context = f"APP_NAME: {app_name} & PAGE_TITLE: {page_title}"

                decision = self.decide_source(llm, query, context)
                source = decision.get("source")


                if source == "page":
                    answer = decision.get("answer", "No answer returned.")
                    updated_context = f"Page Context:\n{context}\n\nSuggested Answer:\n{answer}"
                    print("BROWSER_PAGE: Streaming Page Response")
                    await self.stream_answer(
                        llm=llm,
                        query=query,
                        context=updated_context,
                        websocket=websocket,
                        task_id=task_id,
                        chat_id=chat_id,
                        tool="browser_page",
                    )
                    return

                #TODO: in the search with query also give the page details to search and get results to query and page.
                if source == "search":
                    print("BROWSER_PAGE: 🔎 Page content insufficient. Fetching Tavily results...")
                    tavily_results = self.fetch_tavily_results(query)
                    context = f"Tavily Search Results:\n{self.format_search_results(tavily_results)}"
                    await self.stream_answer(
                        llm=llm,
                        query=query,
                        context=context,
                        websocket=websocket,
                        task_id=task_id,
                        chat_id=chat_id,
                        tool="browser_page",
                    )
                    return


        except Exception as e:
            raise RuntimeError(
                f"Error while running browser page agent: Error -> {str(e)}"
            )
    
    # TODO: Move this to LLM Factory
    def decide_source(self, llm: BaseChatModel, query: str, page_content: str) -> Dict[str, Any]:
        """
        Decides whether to use the page content or web search for the answer.
        """
        system_content = CLASSIFIER_PROMPT.format(page_content=page_content)
        messages = [
            SystemMessage(content=system_content),
            HumanMessage(content=query),
        ]
        response = llm.invoke(messages)
        try:
            return json.loads(response.content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Classifier returned invalid JSON: {response.content}") from exc

    async def stream_answer(
        self,
        llm: BaseChatModel,
        query: str,
        *,
        context: str,
        websocket: WebSocket,
        task_id: str,
        chat_id: str,
        tool: str = "browser_page",
    ) -> str:
        """
        Streams the LLM response to the websocket client chunk by chunk.
        """
        messages = [
            SystemMessage(content=BROWSER_APP_PROMPT.format(context=context.strip())),
            HumanMessage(content=query),
        ]

        collected: List[str] = []

        for chunk in llm.stream(messages):
            text = extract_text(chunk.content)
            if not text:
                continue

            collected.append(text)
            print(text, end="", flush=True)  # Print to console as chunks stream
            await send_ws_message(
                websocket=websocket,
                type="aura_message",
                task_id=task_id,
                chat_id=chat_id,
                payload={
                    "content": {
                        "role": "assistant",
                        "tool": tool,
                        "message": text,
                    }
                },
            )
        
        final_answer = "".join(collected)

        #Storing thr agent response inside DB.
        await create_agent_event(
            pool=self.dbpool,
            task_id=task_id,
            role="assistant",
            message_type="aura_message",
            tool=tool,
            payload={
                "content": {
                    "message": final_answer
                },
            },
        )

        print()  # Newline after streaming completes
        return "".join(collected)

    def fetch_tavily_results(self, query: str, *, max_results: int = 10) -> List[Dict[str, Any]]:
        TAVILY_API_KEY = os.getenv('TAVILY_API_KEY')
        if not TAVILY_API_KEY or "replace-with-tavily-api-key" in TAVILY_API_KEY:
            raise RuntimeError("Set TAVILY_API_KEY constant before running the agent.")
        payload = {
            "query": query,
            "api_key": TAVILY_API_KEY,
            "search_depth": "advanced",
            "max_results": max_results,
        }
        response = requests.post(
            "https://api.tavily.com/search",
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("results", [])

    def format_search_results(self, results: List[Dict[str, Any]]) -> str:
        if not results:
            return "No Tavily search results were returned."

        formatted = []
        for idx, item in enumerate(results, 1):
            snippet = item.get("content") or item.get("snippet") or "No summary available."
            formatted.append(
                f"{idx}. Title: {item.get('title', 'Unknown')}\n"
                f"   URL: {item.get('url', 'N/A')}\n"
                f"   Summary: {snippet}"
            )
        return "\n".join(formatted)

# ------------------------------------------------------------------------- #
# ---------------  Functions To run Recall Agent ------------------------- #
# ------------------------------------------------------------------------- #

    async def run_recall_memory_agent(self):
        """This function runs the recall memory agent to give content you have seen in past """
        try:
            history = self.payload.get("history", [])
            content = "We dont have any history"
            if history:
                history = json.loads(history)
                content = format_history(history)
            
            await self.stream_recall_memory_agent_response(context=content)

            return

        except Exception as e:
            raise RuntimeError(
                f"Error while running recall memory agent: Error -> {str(e)}"
            )

    async def stream_recall_memory_agent_response(self, context: str):
        """
        Streams the LLM response for recall_memory_agent to the websocket client chunk by chunk.
        """
        # Avoid using `.format()` on RECALL_MEMORY_PROMPT because it contains many `{}` braces
        # for JSON examples (e.g. `"url": ...`), which would be interpreted as format fields
        # and cause KeyError like ('"url"',). Instead, safely replace only the `{context}` token.
        prompt = RECALL_MEMORY_PROMPT.replace("{context}", context.strip())

        messages = [
            SystemMessage(content=prompt),
            HumanMessage(content=self.query),
        ]

        result = self.llm.invoke(messages)

        content = result.content

        await send_ws_message(
            websocket=self.websocket,
            type="aura_message",
            task_id=self.task_id,
            chat_id=self.chat_id,
            payload={
                "content": {
                    "role": "assistant",
                    "tool": "recall_memory",
                    "message": content,
                }
            },
        )

        await create_agent_event(
            pool=self.dbpool,
            task_id=self.task_id,
            role="assistant",
            message_type="aura_message",
            tool="recall_memory",
            payload={
                "content": {
                    "message": content,
                },
            },
        )

        print(f"CONTENT: {content}")

        return content
        
        # ** IF WANT TO STREAM THE RESPONSE THEN UNCOMMENT THE BELOW CODE ABD COMMENT ABOVE CODE  **
        # collected: List[str] = []

        # for chunk in self.llm.stream(messages):
        #     text = extract_text(chunk.content)
        #     if not text:
        #         continue

        #     collected.append(text)
        #     print(text, end="", flush=True)  # Print to console as chunks stream
        #     await send_ws_message(
        #         websocket=self.websocket,
        #         type="aura_message",
        #         task_id=self.task_id,
        #         chat_id=self.chat_id,
        #         payload={
        #             "content": {
        #                 "role": "assistant",
        #                 "tool": "recall_memory",
        #                 "message": text,
        #             }
        #         },
        #     )

        # print()  # Newline after streaming completes
        # return "".join(collected)


def extract_text(chunk_content: Any) -> str:
    if isinstance(chunk_content, str):
        return chunk_content

    if isinstance(chunk_content, list):
        text_parts: List[str] = []
        for item in chunk_content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(item.get("text", ""))
        return "".join(text_parts)

    return ""

def format_history(history):
    """
    Format browsing history list/JSON into readable LLM-ready plain text.

    Example output:
    (url: ..., title: ..., last_visited_time: ..., favicon: ...)
    (url: ..., title: ..., last_visited_time: ..., favicon: ...)
    """

    # If history was passed as JSON string, convert to list
    if isinstance(history, str):
        try:
            history = json.loads(history)
        except json.JSONDecodeError:
            return "History parsing failed."

    if not history:
        return "No browsing history found in the last two days."

    formatted_lines = []
    for item in history:
        formatted_lines.append(
            f"(url: {item.get('url', 'N/A')}, "
            f"title: {item.get('title', 'N/A')}, "
            f"last_visited_time: {item.get('last_visited_time', 'N/A')}, "
            f"favicon: {item.get('favicon', 'N/A')})"
        )

    return "\n".join(formatted_lines)



