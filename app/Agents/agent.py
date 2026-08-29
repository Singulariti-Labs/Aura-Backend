from typing import Optional, List, Any, Dict, Union
from fastapi import WebSocket
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from asyncpg import Pool


from app.Agents.base_agent import BaseAgent
from app.Types.agent_types import LLMConfig, SystemInfo, AuraConfig
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
from app.Prompts.aura_new import buildAuraSystemPrompt
from app.helper import update_memory

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
        screenshot: Optional[Union[Dict[str, Any], List[str], str]] = None,
        pool: Pool | None = None,
        user_id: Optional[str] = None,
        aura_config: Optional[AuraConfig] = None,
        history: List[Dict] = [],
        attached_files: Optional[List[Dict[str, Any]]] = None,
        attached_images: Optional[List[Dict[str, Any]]] = None,
        rate_limit_loop: Optional[Any] = None,
        runtime_task_id: Optional[str] = None,
        compression_id: Optional[str] = None,
    ):
        self.query = query
        self.task_id = task_id
        self.runtime_task_id = runtime_task_id or task_id
        self.compression_id = compression_id
        self.chat_id = chat_id
        self.dbpool = pool
        self.user_id = user_id
        self.rate_limit_loop = rate_limit_loop
        self.llm_config = llm
        self.llm_provider = llm.provider
        # BYOK calls are still audited, but their provider cost must not consume
        # the platform-funded subscription allowance.
        self.credential_source = llm.credential_source or (
            "custom" if llm.api_key else "platform"
        )
        self.memory = Memory()
        self.llm_factory = LLMFactory(
            self.memory,
            rate_limit_pool=self.dbpool,
            user_id=self.user_id,
            rate_limit_loop=self.rate_limit_loop,
            fallback_provider=self.llm_config.provider,
            fallback_model_name=self.llm_config.model_name,
            credential_source=self.credential_source,
        )
        self.llm = LLMFactory.create_llm(llm, user_api_key=llm.api_key)
        self.max_tokens = maxTokens
        self.system_info = system_info
        
        # Normalize screenshot parameter to List[str] of base64 strings
        processed_screenshot = None
        if screenshot:
            if isinstance(screenshot, dict):
                processed_screenshot = [screenshot.get("data") or screenshot.get("content") or screenshot.get("image_base64")]
            elif isinstance(screenshot, str):
                processed_screenshot = [screenshot]
            else:
                processed_screenshot = screenshot
        self.screenshot = processed_screenshot
        self.agent_prompt = AGENT_PROMPT
        self.history = history
        self.aura_config = aura_config or AuraConfig()
        # Runtime-bound tools must use the scheduler's operation ID. For normal
        # tasks this equals task_id; standalone compression uses compression_id.
        self.tools = Tools(llm=self.llm, memory=self.memory, task_id=self.runtime_task_id, chat_id=self.chat_id, system_info=self.system_info, aura_config=aura_config, history=self.history, llm_provider=self.llm_provider, dbpool=self.dbpool, user_id=self.user_id, rate_limit_loop=self.rate_limit_loop, credential_source=self.credential_source)
        self.payload = payload
        self.attached_files = attached_files
        self.attached_images = attached_images
        
    # Runs the Aura Agent.
    async def invoke(self):
        """
        Executes the Aura Agent by sending a user query and optional screenshot to the LLM with the configured tools.

        This method performs the following:
        - Constructs a user message from the query and optional screenshot.
        - Stores the message in memory to maintain chat history.
        - Retrieves available tools for the agent.
        - Calls the Aura executor with the query, chat history, tools, system prompt, system info and aura config.
        - Returns the result produced by the agent.

        Raises:
            RuntimeError: If an error occurs while invoking the agent or creating the LLM instance.
        """

        try:
            # Get web socket from task manager
            task_state = task_manager.get_state(self.runtime_task_id)
            self.websocket = task_state.websocket
            compression_only = bool(
                self.payload.get("_force_preflight_compression", False)
            )

            # Notify client present inside Main Agent
            if not compression_only:
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

            # chat_history = self.memory.messages
            try:
                # ⏸ Pause check before any heavy work
                await task_manager.wait_if_paused(self.runtime_task_id)

                available_tools = (
                    [] if compression_only else self.tools.get_agent_tools()
                )

                # ❌ Optional cancel check (recommended)
                if task_manager.get_state(self.runtime_task_id).cancelled:
                    raise asyncio.CancelledError()
                
                # ⏸ Pause check again before the LLM call
                await task_manager.wait_if_paused(self.runtime_task_id)
                result = None

                #Get llm provider
                llm_provider = self.llm_config.provider

                # If the option is not complex_task or smart then run the main agent
                if (
                    not compression_only
                    and self.payload.get('option') not in ["complex_task", "smart"]
                ):
                    context_agent = ContextAgent(
                        query=self.query,
                        payload=self.payload,
                        task_id=self.task_id,
                        chat_id=self.chat_id,
                        llm=self.llm,
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
                
                elif self.payload.get("option") == "general":
                    print("GENERAL AI: Running General Agent")
                    result = await context_agent.run_general_agent()

                else:
                    # Calling Aura Agent
                    # Get all the tools for the Aura
                    tools = self.tools.get_supervisor_tools()

                    prompt = buildAuraSystemPrompt(
                        system_info=self.system_info,
                        tools=tools,
                        chat_id=self.chat_id,
                        task_id=self.task_id,
                        config=self.aura_config,
                    )

                    result = None
                    # result = await self.llm_factory.aura_executor(
                    #     query=self.query,
                    #     system_prompt=prompt,
                    #     tools=tools,
                    #     attached_files=self.attached_files,
                    #     attached_images=self.attached_images,
                    #     system_info=self.system_info,
                    #     llm=self.llm,
                    #     agent_type="aura",
                    #     history=self.history,
                    #     llm_provider=llm_provider,
                    #     screenshot=self.screenshot
                    # )

                    result = await self.llm_factory.aura_invoker(
                        query=self.query,
                        system_prompt=prompt,
                        tools=tools,
                        attached_files=self.attached_files,
                        attached_images=self.attached_images,
                        system_info=self.system_info,
                        llm=self.llm,
                        agent_type="aura",
                        history=self.history,
                        llm_provider=llm_provider,
                        screenshot=self.screenshot,
                        max_tokens=self.max_tokens,
                        task_id=self.task_id,
                        runtime_task_id=self.runtime_task_id,
                        chat_id=self.chat_id,
                        compression_id=self.compression_id,
                        compression_enabled=self.aura_config.compression,
                        force_preflight_compression=bool(
                            self.payload.get("_force_preflight_compression", False)
                        ),
                        compression_range=self.payload.get("range"),
                        compression_reason=(
                            self.payload.get("trigger")
                            if isinstance(self.payload.get("trigger"), str)
                            else "compression_request"
                        ),
                    )

                    final_result = None
                    if "output" in result:
                        final_result = result.get("output")
                    else:
                        final_result = "Aura LLM run failed, task failed to complete successfull."

                    if not compression_only:
                        # SEND_RESPONSE_TO_CLIENT - Aura Agent output
                        final_sequence = next(
                            (
                                message.get("sequence")
                                for message in reversed(result.get("messages", []))
                                if message.get("role") == "assistant"
                            ),
                            None,
                        )
                        await send_ws_message(
                            websocket=self.websocket,
                            task_id=self.task_id,
                            chat_id=self.chat_id,
                            type="aura_message",
                            payload={
                                "content": {
                                    "role": "assistant",
                                    "message": final_result,
                                    "sequence": final_sequence,
                                },
                                "coming_from": "aura_agent/server"
                            }
                        )

                        update_memory(
                            role="assistant",
                            content=final_result,
                            memory=self.memory,
                        )

                # SEND_STATUS_TO_CLIENT - Aura Run Completed
                print(f"\n\n----- AGENT RUN FINISHED -----\n\n")

                if not compression_only:
                    await send_ws_message(
                        websocket=self.websocket,
                        type="aura_status",
                        task_id=self.task_id,
                        chat_id=self.chat_id,
                        payload={
                            "query": self.query,
                            "message": "<AURA> run completed",
                            "status": "completed",
                        }
                    )
                return result
            
            except Exception as e:
                # This catches errors that happen inside the try block (like tool execution)
                raise RuntimeError(
                    f"Error while calling Agent: {str(e)}"
                )

        except Exception as e:
            # If we already have a RuntimeError from the inner block, re-raise it
            if isinstance(e, RuntimeError) and "Error while calling Agent" in str(e):
                raise e
            # Otherwise, it might actually be an LLM initialization error (if it failed in __init__)
            raise RuntimeError(
                f"Error initializing or invoking Agent: {str(e)}"
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



