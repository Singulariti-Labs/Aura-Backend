from typing import Optional, List, Any, Dict
from fastapi import WebSocket
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from app.Agents.base_agent import BaseAgent
from app.Types.agent_types import LLMConfig, SystemInfo
from app.LLM.llm_factory import LLMFactory
from app.LLM.memory import Message, Memory
from app.Prompts.agent import AGENT_PROMPT
from app.Prompts.classifier_prompt import CLASSIFIER_PROMPT
from app.Prompts.browser_page_option import BROWSER_PAGE_PROMPT
from app.Tools.tool_calling import Tools
from app.Task.task_manager import task_manager
from app.api.websocket_utils import send_ws_message
from app.Option_Helper.web_scraper import simple_web_scraper

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
        screenshot:  Optional[List[str]] = None
    ):
        self.query = query
        self.task_id = task_id
        self.chat_id = chat_id
        self.llm_config = llm
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
        

    async def invoke(self):
        """
        Executes the main agent by sending a user query and optional screenshot to the LLM with the configured tools.

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

                if self.payload.get("option") == "browser_page":
                    print("BROWSER_PAGE: running browserr page agent")
                    result = await self.run_browser_page_agent(
                        websocket=self.websocket,
                        query=self.query,
                        payload = self.payload,
                        screenshot=self.screenshot,
                        llm=self.llm,
                        task_id=self.task_id,
                        chat_id=self.chat_id
                    )
                else:
                    result = await self.llm_factory.agent_executor(
                        llm=self.llm,
                        query=self.query,
                        screenshot=self.screenshot,
                        system_prompt=self.agent_prompt,
                        chat_history=chat_history,
                        tools=available_tools,
                        system_info=self.system_info,
                        agent_type="main"
                    )

                # SEND_RESPONSE_TO_CLIENT - Agent output

                print(f"RESULT OF AGENT: {result}")
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
            SystemMessage(content=BROWSER_PAGE_PROMPT.format(context=context.strip())),
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

