from dotenv import load_dotenv
from typing import Optional, List, Union, Dict, Any
import asyncio
from functools import partial
from langchain_openai.chat_models.base import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_classic.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import Runnable
from langchain_core.tools import Tool
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.runnables import RunnableLambda, RunnableConfig
from langchain_core.messages import HumanMessage, SystemMessage
import os
import re
import json
import time


from app.Types.agent_types import LLMConfig, StepsList, SystemInfo, AGENT_TYPE
from app.LLM.memory import Message
from app.LLM.memory import Memory
from app.helper import update_memory, update_input_messages_with_screenshot_and_context
from app.Prompts.validator import VALIDATOR_PROMPT
from app.Prompts.classifier_prompt import CLASSIFIER_PROMPT
from app.handler import AgentCallbackHandler, MaxOutputTokenLimitError
from app.LLM.model_token_limits import (
    get_model_context_profile,
    get_model_max_output_tokens,
    resolve_open_router_model,
)
from app.Context import CompressionConfig, ContextManager, context_store
from app.Context.compression_llm import (
    AnthropicCompressionService,
    compression_llm_service,
)
from app.Task.task_manager import task_manager
from app.api.websocket_utils import send_ws_message
from app.utils.format_messages import format_to_langchain
from app.utils.tool_message_formatter import format_multimodal_tool_messages
from app.Adapters.format_message import prepareMessageForAI
from app.LLM.model_bridge.anthropic import (
    anthropic_message_formater,
    anthropic_response_formater,
    anthropic_tool_formater,
    invoke_anthropic_messages,
)
from app.LLM.model_bridge.common import (
    build_user_message,
    canonical_tool_result,
    configured_output_limit,
    model_name_from_llm,
    normalize_history,
)
from app.LLM.model_bridge.gemini import (
    gemini_message_formater,
    gemini_response_formater,
    gemini_tool_formater,
    gemini_tool_result_formater,
    invoke_gemini_generate_content,
)
from app.LLM.model_bridge.openai import (
    openai_message_formater,
    openai_response_formater,
    openai_tool_formater,
    invoke_openai_chat_completions,
)
from app.RateLimit.rate_limit_service import schedule_token_usage_update
from app.RateLimit.token_pricing import calculate_token_cost_usd_float
from datetime import datetime


load_dotenv()

class LLMFactory():
    """
    LLMFactory handles creation and execution of language model agents with optional tools, multimodal inputs, 
    and memory support for chat history.
    """

    def __init__(
        self,
        memory: Memory,
        rate_limit_pool: Optional[Any] = None,
        user_id: Optional[str] = None,
        fallback_provider: Optional[str] = None,
        fallback_model_name: Optional[str] = None,
        credential_source: str = "platform",
        rate_limit_loop: Optional[Any] = None,
    ):
        """
        Initializes the LLMFactory with a memory instance to track chat history and message flow.

        Input:
        - memory: An instance of the Memory class to persist user and assistant messages.
        """
        self.memory = memory
        self.rate_limit_pool = rate_limit_pool
        self.user_id = user_id
        self.rate_limit_loop = rate_limit_loop
        self.fallback_provider = fallback_provider
        self.fallback_model_name = fallback_model_name
        self.credential_source = (
            credential_source if credential_source in {"platform", "custom"}
            else "platform"
        )

    @staticmethod
    def create_llm(llm_config: LLMConfig, user_api_key: str = None):
        """
        Creates a language model instance based on the given provider and model name.

        Input:
        - llm_config: Validated provider, model, and credential configuration.

        Returns:
        - An instance of ChatOpenAI, ChatAnthropic, or ChatGoogleGenerativeAI.
        """
        try:
            # Output limits are controlled by the backend model table. They
            # are intentionally not accepted from the task_request payload.
            max_output_tokens = get_model_max_output_tokens(
                llm_config.provider,
                llm_config.model_name,
            )

            if llm_config.provider == "openai":
                api_key = user_api_key or os.environ.get("OPENAI_API_KEY")
                
                if not api_key:
                    raise ValueError("OPENAI_API_KEY environment variable is not set")

                return ChatOpenAI(
                    model=llm_config.model_name,
                    api_key=api_key,
                    stream_usage=True,
                    max_tokens=max_output_tokens,
                )
            
            elif llm_config.provider == "anthropic":
                api_key = user_api_key or os.environ.get("ANTHROPIC_API_KEY")
                 
                if not api_key:
                    raise ValueError("ANTHROPIC_API_KEY environment variable is not set")
                
                return ChatAnthropic(
                    model=llm_config.model_name,
                    api_key=api_key,
                    max_tokens_to_sample=max_output_tokens,
                )
            
            elif llm_config.provider == "open_router":
                api_key = user_api_key or os.environ.get("OPENROUTER_API_KEY")
                
                if not api_key:
                    raise ValueError("OPENROUTER_API_KEY environment variable is not set")

                return ChatOpenAI(
                    model=resolve_open_router_model(llm_config.model_name),
                    api_key=api_key,
                    base_url="https://openrouter.ai/api/v1",
                    max_tokens=max_output_tokens,
                )
            
            elif llm_config.provider == "google":
                api_key = user_api_key or os.environ.get("GOOGLE_API_KEY")
                
                if not api_key:
                    raise ValueError("GOOGLE_API_KEY environment variable is not set")
                
                if llm_config.model_name == "gemini-3-flash-preview":
                    return ChatGoogleGenerativeAI(
                        model=llm_config.model_name, 
                        api_key=api_key,
                        thinking_level="low",
                        max_tokens=max_output_tokens,
                        model_kwargs={
                            "tool_config": {
                                "function_calling_config": {
                                    "mode": "ANY"
                                }
                            }
                        }
                    )
                return ChatGoogleGenerativeAI(
                    model=llm_config.model_name,
                    api_key=api_key,
                    max_tokens=max_output_tokens,
                    model_kwargs={
                        "tool_config": {
                            "function_calling_config": {
                                "mode": "ANY"
                            }
                        }
                    })
            
            elif llm_config.provider == "agent_router":
                api_key = user_api_key or os.environ.get("AGENTROUTER_API_KEY")
                
                if not api_key:
                    raise ValueError("AGENT_ROUTER_API_KEY environment variable is not set")

                return ChatOpenAI(
                    model=llm_config.model_name,
                    api_key=api_key,
                    base_url="https://api.agentrouter.com/v1",
                    max_tokens=max_output_tokens,
                )
            
            else:
                raise ValueError(f"Unsupported provider: {llm_config}")
        except Exception as e:
            raise RuntimeError(
                f"Error creating LLM instance for provider '{llm_config}' "
                f"with model '{llm_config.model_name}': {str(e)}"
            )
        
    @staticmethod
    def get_agent_type(llm: BaseChatModel, prompt: ChatPromptTemplate, tools: Optional[List[Tool]] = None):
        """
        Determines the appropriate agent creation method based on the type of LLM.

        Input:
        - llm: A BaseChatModel instance.
        - prompt: A ChatPromptTemplate to guide the agent's behavior.
        - tools: Optional list of tools to be used by the agent.

        Returns:
        - An agent configured with the provided tools and LLM.
        """
        print(f"PROMT TYPE: {type(prompt)}")
        if isinstance(llm, ChatOpenAI):
            return create_tool_calling_agent(
                llm,
                tools,
                prompt,
                message_formatter=lambda steps: format_multimodal_tool_messages(
                    steps,
                    provider="openai",
                ),
            )
        elif isinstance(llm, ChatAnthropic):
            return create_tool_calling_agent(
                llm,
                tools,
                prompt,
                message_formatter=lambda steps: format_multimodal_tool_messages(
                    steps,
                    provider="anthropic",
                ),
            )
        else:
            raise ValueError(f"Unsupported LLM type: {type(llm)}")
    
    @staticmethod
    def invoke_agent(
        llm:BaseChatModel,
        agent: Runnable,
        tools: List[Tool],
        system_info: SystemInfo,
        query: Union[str, List[Dict[str, Union[str, dict]]]],
        chat_history: Optional[List[Message]] = [],
    ):
        """
        Invokes an agent with the given input, tools, and memory context.

        Input:
        - llm: The language model used by the agent.
        - agent: The runnable agent instance.
        - tools: List of tools to assist the agent.
        - system_info: Optional system-level context.
        - query: The user input (text or multimodal).
        - chat_history: Optional list of previous messages.

        Returns:
        - The final response from the agent after execution.
        """
        agent_executor = AgentExecutor(
            agent=agent,
            tools=tools,
            verbose=True,
            return_intermediate_steps=True
        )
        result = agent_executor.invoke({"input": query,  "chat_history_for_llm": chat_history, "system_info": system_info})
        return result
    
    def get_multimodal_query(self, query: str, base64_images:  Optional[List[str]] = None, current_state: Optional[str] = None):
        """
        Formats a multimodal query combining user text and screenshot data.

        Input:
        - query: User's textual input.
        - base_images: A list of base64 image string or URL.

        Returns:
        - A list combining text and image input formatted for multimodal LLMs.
        """

        multimodal_query = []


        # Add the user's actual question
        multimodal_query.append({
            "type": "text",
            "text": query
        })

         # If current state exists, add it first
        if current_state:
            multimodal_query.append({
                "type": "text",
                "text": f"Parsed Page Info:\n{current_state}"
            })

        # Add the image if provided
        if base64_images:
            multimodal_query.extend([
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{img}"  # JPEG format make png
                    }
                }
                for img in base64_images
        ])

        return multimodal_query

    
    async def agent_executor( #WIP
        self,
        system_prompt: str,
        llm: BaseChatModel,
        query: str,
        agent_type: AGENT_TYPE,
        system_info: Optional[SystemInfo | str] = None,
        tools: Optional[List[Tool]] = None,
        chat_history: Optional[List[Message]] = None,
        screenshot:  Optional[List[str]] = None,
        llm_provider: Optional[str] = None,
        max_tokens: int = 128000,
    ):
        """
        Executes a user query using the agent or directly via LLM, with optional tools, chat history, and image input.
        * It is only used for calling agents who has async run method.

        Input:
        - system_prompt: Initial system prompt to guide the agent.
        - llm: The language model to use.
        - query: The user's question or command.
        - system_info: Optional system-specific information.
        - tools: Optional list of tools the agent can use.
        - chat_history: Optional list of previous messages to maintain continuity.
        - screenshot: Optional image input in base64 or URL.
        - llm_provider: Optional LLM provider to use.
        - max_tokens: Maximum token limit for the LLM response.

        Returns:
        - The response from the agent or LLM after processing the query.
        """
        try:
            # if screenshot:
            #     multimodal_query = self.get_multimodal_query(query=query, base64_images=screenshot)
            # else:
            #     multimodal_query = query

                # Add the user's query in the memory -> (User query added in Agent class)
                # user_query = Message.user_message(content=query, base64_image=screenshot)
                # self.memory.add_message(user_query)


            # Prepare chat history
            if chat_history:
                chat_history_for_llm = [message.to_dict() for message in chat_history]

            # update_memory("user", content=query, base64_images=screenshot, memory=self.memory)
            
            # Prepare system prompt
            if system_prompt:
                prompt = ChatPromptTemplate.from_messages([
                    ("system", system_prompt),
                    MessagesPlaceholder(variable_name="chat_history"),
                    ("human", "{input}"),
                    MessagesPlaceholder(variable_name="agent_scratchpad")
                ])
        
            # Format system info
            system_info_str = system_info
            if system_info and isinstance(system_info, SystemInfo):
                system_info_str = f"OS: {system_info.os}, Version: {system_info.version}, Workspace: {system_info.workspace}, CWD: {system_info.cwd}"

            
            # Format input
            formated_input = (
                f"query: {query}\n"
                f"system_info: {system_info_str}\n"
            )
            
            # If tools are provided, use a tool-calling agent.
            if tools:
                # agent = self.get_agent_type(llm=llm, prompt=prompt, tools=tools)
                agent = self.create_agent_for_provider(
                    llm=llm,
                    prompt=prompt,
                    tools=tools,
                    llm_provider=llm_provider
                )
                handler = AgentCallbackHandler(
                    self.memory,
                    rate_limit_pool=self.rate_limit_pool,
                    user_id=self.user_id,
                    rate_limit_loop=self.rate_limit_loop,
                    fallback_provider=self.fallback_provider,
                    fallback_model_name=self.fallback_model_name,
                    fallback_credential_source=self.credential_source,
                )

                executor = AgentExecutor(
                    agent=agent,
                    tools=tools,
                    verbose=True,
                    return_intermediate_steps=True,
                )

                response = await executor.ainvoke(
                    {"input": formated_input, "chat_history": chat_history_for_llm},
                    config=RunnableConfig(callbacks=handler.as_list()),
                )
                
                return response
            else:
                # Invoke the LLM
                response = await llm.ainvoke({"input": formated_input})

                # adding response to memory as assistant message.
                assistant_message = Message.assistant_message(content=response.content)
                self.memory.add_message(assistant_message);
                return response
        
        except MaxOutputTokenLimitError:
            raise
        except Exception as e:
            raise RuntimeError(f"Failed to execute agent or LLM call: {str(e)}")
            

    async def invoke_planner_agent(self, llm: BaseChatModel, prompt_template: str, query: str) -> List[Dict[str, str]]:
        """
        Uses a language model to generate a structured multi-step plan from a user query.

        Input:
        - llm: The language model instance to generate the plan.
        - prompt_template: Template string with placeholders for query and formatting.
        - query: The complex input question to be broken down.

        Returns:
        - A list of steps, each with id, description, thought, dependency, and expected output.
        """
        try:
            parser = PydanticOutputParser(pydantic_object=StepsList)
            format_instructions = parser.get_format_instructions()
            
            # Fill in the placeholders
            prompt = prompt_template.format(query=query, format_instructions=format_instructions)
            
            # Get model response
            try:
                response = llm.predict(prompt)
            except Exception as e:
                raise Exception(f"Error while invoking the LLM in Planner: {e}")
            
            # Parse structured output
            try:
                result = parser.parse(response)
            except Exception as e:
                raise ValueError(f"Error parsing the response: {e}")

            # Extract final steps in a clean list of dictionaries
            final_result = [{
                "id": step.id,
                "description": step.description,
                "thought": step.thought,
                "dependency": step.dependency,
                "expected_output": step.expected_output
            } for step in result.steps]
            
            return final_result
        
        except Exception as e:
            raise Exception(f"Error while creating a plan: {e}")


    async def invoke_interaction_agent(
        self,
        llm: BaseChatModel,
        input_message: List[dict],
        agent_type: AGENT_TYPE,  # type: ignore
        base64_image: Optional[str] = None,
        parsed_screen_context: Optional[str] = None,  #(parsed_page)
        max_tokens = 128000,
    ):
        try:
            llm_invoke_message = input_message 

            if base64_image or parsed_screen_context:
                llm_invoke_message = update_input_messages_with_screenshot_and_context(input_message=input_message, base64_image=base64_image, parsed_screen_context=parsed_screen_context)
            
            response = await llm.ainvoke(llm_invoke_message)

            parsed_content = self.parse_response_in_json(response_content=response.content)

            print(f"INTERACTION_PARSED_CONTENT: {parsed_content}")

            return parsed_content
            
        except Exception as e:
            raise RuntimeError(f"Error while invoking Interaction agent: {e}")

    
    def parse_response_in_json(self, response_content: str):
        """
        Extracts a JSON code block from markdown-style text (```json ... ```),
        parses it, and returns the resulting dictionary.

        Parameters:
            response_content (str): The text content containing a JSON code block.

        Returns:
            dict: The parsed JSON as a dictionary, if found.
            None: If no JSON block is found or parsing fails.
        """
        match = re.search(r'```json\s*(\{.*?\})\s*```', response_content, re.DOTALL)
        
        if match:
            json_string = match.group(1)
        else:
            # If no code block found, assume it's a plain JSON string
            json_string = response_content.strip()
        
        try:
            data_dict = json.loads(json_string)
            # print("Parsed JSON:", data_dict)
            return data_dict
        except json.JSONDecodeError as e:
            print("Failed to parse JSON:", e)
            return None
        
    async def response_validator(self, llm: BaseChatModel, query:str, response: str, expected_output: str) -> bool:
        """
        Validates the response against the expected output.
        """
        try:
            validator_prompt = VALIDATOR_PROMPT

            prompt = ChatPromptTemplate.from_messages([
                ("system", validator_prompt),
                ("human", query),
                ("human", response),
                ("human", expected_output)
            ])

            input_message = prompt.format_messages(
                    validator_prompt = validator_prompt,
                    query = query,
                    response = response,
                    expected_output = expected_output
                )
            
            result = await llm.ainvoke(input_message)

            parsed_content = self.parse_response_in_json(response_content=result.content)

            return parsed_content
        except Exception as e:
            raise RuntimeError(f"Falied To Validate Response: {e}")


    async def aura_invoker(
            self,
            system_prompt: str,
            llm: BaseChatModel,
            query: str,
            llm_provider: str,
            agent_type: AGENT_TYPE,
            system_info: Optional[SystemInfo | str] = None,
            tools: Optional[List[Tool]] = None,
            chat_history: Optional[List[Message]] = None,
            base_64_image: Optional[List[str]] = None,
            attached_files: Optional[List[Dict[str, Any]]] = None,
            attached_images: Optional[List[Dict[str, Any]]] = None,
            max_tokens: int = 128000,
            history: Optional[List[Dict[str, Any]]] = None,
            screenshot: Optional[Any] = None,
            task_id: Optional[str] = None,
            chat_id: Optional[str] = None,
            compression_enabled: bool = True,
            force_preflight_compression: bool = False,
            compression_range: Optional[Dict[str, Any]] = None,
            compression_reason: str = "preflight",
            runtime_task_id: Optional[str] = None,
            compression_id: Optional[str] = None,
        ) -> Dict[str, Any]:
        """Run Aura's lightweight native model-and-tool orchestration loop.

        This method coordinates only the high-level lifecycle: prepare the
        provider request, invoke the model, track usage, execute requested
        tools, and return the final response. The detailed routing, formatting,
        parsing, and accounting logic lives in the focused helpers immediately
        below this method.

        ``chat_history`` remains for drop-in compatibility. The provider-neutral
        ``history`` argument is the authoritative conversation history.
        """

        del chat_history
        provider = self._route_aura_provider(llm_provider, llm)

        user_message = None
        if not force_preflight_compression:
            user_message = self._build_aura_user_message(
                query=query,
                system_info=system_info,
                attached_files=attached_files,
                attached_images=attached_images,
                base_64_image=base_64_image,
                screenshot=screenshot,
            )
        canonical_messages, native_messages = self._normalize_aura_conversation(
            history=history,
            user_message=user_message,
            provider=provider,
            system_prompt=system_prompt,
        )

        tool_map, native_tools = self._build_aura_tool_registry(tools, provider)
        model_name = model_name_from_llm(llm)
        output_limit = configured_output_limit(llm, max_tokens)

        resolved_task_id = str(task_id or f"ephemeral-{id(self)}")
        resolved_runtime_task_id = str(runtime_task_id or resolved_task_id)
        resolved_chat_id = str(chat_id or "ephemeral")

        async def send_context_event(event: Dict[str, Any]) -> None:
            if task_id is None:
                return
            state = task_manager.get_state_or_none(resolved_runtime_task_id)
            if state is None or state.websocket is None or state.connection_closed:
                return
            event_type = event.get("type")
            event_compression_id = event.get("compression_id") or compression_id
            if force_preflight_compression and event_type != "compression":
                # A standalone manual-compression request has its own protocol
                # and must not leak Aura progress or context-sequence events.
                return
            if event_type == "compression":
                await send_ws_message(
                    websocket=state.websocket,
                    type="compression",
                    task_id=resolved_task_id,
                    chat_id=resolved_chat_id,
                    payload=event,
                    compression_id=event_compression_id,
                )
            elif event_type == "context_sequence":
                await send_ws_message(
                    websocket=state.websocket,
                    type="context_sequence",
                    task_id=resolved_task_id,
                    chat_id=resolved_chat_id,
                    payload=event,
                )
            else:
                await send_ws_message(
                    websocket=state.websocket,
                    type="aura_status",
                    task_id=resolved_task_id,
                    chat_id=resolved_chat_id,
                    compression_id=event_compression_id,
                    payload={
                        "compression_id": event_compression_id,
                        "message": event.get("message"),
                        "status": event.get("status"),
                        "context_id": event.get("context_id"),
                    },
                )

        compression_config = CompressionConfig(enabled=compression_enabled)
        context_manager = ContextManager(
            task_id=resolved_task_id,
            chat_id=resolved_chat_id,
            agent_id=str(agent_type),
            provider=provider,
            model=model_name,
            profile=get_model_context_profile(provider, model_name),
            messages=canonical_messages,
            store=context_store,
            config=compression_config,
            client_event_callback=send_context_event,
            compression_id=compression_id,
        )
        await context_manager.initialize()
        if task_id is not None and task_manager.get_state_or_none(resolved_runtime_task_id):
            task_manager.register_context(
                resolved_runtime_task_id,
                context_manager.context_id,
            )
            latest_user = next(
                (
                    message
                    for message in reversed(context_manager.snapshot.canonical_messages)
                    if message.get("role") == "user"
                ),
                None,
            )
            if latest_user is not None:
                await send_context_event(
                    {
                        "type": "context_sequence",
                        "context_id": context_manager.context_id,
                        "role": "user",
                        "sequence": latest_user.get("sequence"),
                    }
                )
        canonical_messages = context_manager.snapshot.canonical_messages
        native_messages = self._format_aura_messages(
            provider=provider,
            canonical_messages=context_manager.effective_messages(),
            system_prompt=system_prompt,
        )

        generated_messages: List[Dict[str, Any]] = []
        intermediate_steps: List[Dict[str, Any]] = []
        aggregate_usage = self._empty_aura_usage()
        compressor_summarizer = partial(
            self._summarize_dedicated_context,
            service=compression_llm_service,
            model=compression_config.compressor_model,
            max_tokens=compression_config.compressor_max_output_tokens,
            aggregate_usage=aggregate_usage,
        )

        preflight_pending = force_preflight_compression
        for iteration in range(200):
            requested_range = compression_range if preflight_pending else None

            try:
                compression_event = await context_manager.compress_if_needed(
                    system_prompt=system_prompt,
                    native_tools=native_tools,
                    summarizer=compressor_summarizer,
                    force=preflight_pending,
                    reason=(
                        compression_reason
                        if preflight_pending
                        else "runtime_threshold"
                    ),
                    requested_range=requested_range,
                )
            except Exception as exc:
                if not force_preflight_compression:
                    raise
                compression_event = context_manager.terminal_compression_event(
                    status="failed",
                    message=str(exc),
                    error_code="COMPRESSION_FAILED",
                )
                await send_context_event(compression_event)
            preflight_pending = False
            if compression_event is not None:
                canonical_messages = context_manager.snapshot.canonical_messages
                native_messages = self._format_aura_messages(
                    provider=provider,
                    canonical_messages=context_manager.effective_messages(),
                    system_prompt=system_prompt,
                )

            if force_preflight_compression:
                if context_manager.snapshot.compressor_state.status == "failed":
                    if compression_event is None:
                        failure_message = str(
                            context_manager.snapshot.compressor_state.last_error
                            or "Context compression failed"
                        )
                        compression_event = (
                            context_manager.terminal_compression_event(
                                status="failed",
                                message=failure_message,
                                error_code="COMPRESSION_FAILED",
                            )
                        )
                        await send_context_event(compression_event)
                elif compression_event is None:
                    compression_event = context_manager.terminal_compression_event(
                        status="already_compact",
                        message="Context is already compact",
                    )
                    await send_context_event(compression_event)
                summary = (
                    compression_event.get("summary")
                    or compression_event.get("message")
                )
                return {
                    "output": summary,
                    "messages": [],
                    "intermediate_steps": [],
                    "usage": aggregate_usage,
                    "provider": provider,
                    "model": model_name,
                    "iterations": 0,
                    "agent_type": agent_type,
                    "compression": compression_event,
                }

            started_at = time.perf_counter()
            try:
                raw_response = await self._invoke_aura_model(
                    provider=provider,
                    llm=llm,
                    model_name=model_name,
                    system_prompt=system_prompt,
                    native_messages=native_messages,
                    native_tools=native_tools,
                    output_limit=output_limit,
                )
            except Exception as exc:
                if not self._is_context_length_error(exc):
                    raise
                recovery_event = await context_manager.compress_if_needed(
                    system_prompt=system_prompt,
                    native_tools=native_tools,
                    summarizer=compressor_summarizer,
                    force=True,
                    reason="provider_context_error",
                )
                if recovery_event is None:
                    raise
                native_messages = self._format_aura_messages(
                    provider=provider,
                    canonical_messages=context_manager.effective_messages(),
                    system_prompt=system_prompt,
                )
                raw_response = await self._invoke_aura_model(
                    provider=provider,
                    llm=llm,
                    model_name=model_name,
                    system_prompt=system_prompt,
                    native_messages=native_messages,
                    native_tools=native_tools,
                    output_limit=output_limit,
                )
            parsed = self._parse_aura_response(provider, raw_response)
            duration_ms = (time.perf_counter() - started_at) * 1000
            tool_calls = parsed.get("tool_calls") or []
            usage, details = self._track_aura_usage_and_cost(
                provider=provider,
                model_name=model_name,
                parsed_response=parsed,
                duration_ms=duration_ms,
                aggregate_usage=aggregate_usage,
            )
            context_manager.update_usage(usage)

            finish_reason = str(parsed.get("finish_reason") or "").lower()
            if finish_reason in {"max_tokens", "max_token", "length"}:
                raise MaxOutputTokenLimitError(details=details, usage=usage)

            assistant_message = await context_manager.record_assistant(
                parsed["message"]
            )
            await send_context_event(
                {
                    "type": "context_sequence",
                    "context_id": context_manager.context_id,
                    "role": "assistant",
                    "sequence": assistant_message.get("sequence"),
                    "tool_call_ids": [
                        block.get("tool_call_id")
                        for block in assistant_message.get("content", [])
                        if block.get("type") == "tool_call"
                    ],
                }
            )
            canonical_messages = context_manager.snapshot.canonical_messages
            generated_messages.append(assistant_message)

            if not tool_calls:
                return self._build_aura_result(
                    parsed_response=parsed,
                    generated_messages=generated_messages,
                    intermediate_steps=intermediate_steps,
                    aggregate_usage=aggregate_usage,
                    provider=provider,
                    model_name=model_name,
                    iterations=iteration + 1,
                    agent_type=agent_type,
                )

            raw_tool_results = await asyncio.gather(
                *[
                    self._execute_native_tool_call(tool_call, tool_map)
                    for tool_call in tool_calls
                ]
            )
            tool_results = await context_manager.record_tool_batch(
                raw_tool_results
            )
            for tool_result in tool_results:
                await send_context_event(
                    {
                        "type": "context_sequence",
                        "context_id": context_manager.context_id,
                        "role": "tool",
                        "sequence": tool_result.get("sequence"),
                        "tool_call_id": tool_result.get("tool_call_id"),
                    }
                )
            canonical_messages = context_manager.snapshot.canonical_messages
            self._record_aura_tool_exchange(
                provider=provider,
                parsed_response=parsed,
                tool_calls=tool_calls,
                tool_results=tool_results,
                canonical_messages=None,
                native_messages=native_messages,
                generated_messages=generated_messages,
                intermediate_steps=intermediate_steps,
            )

        raise RuntimeError(
            "aura_invoker reached the maximum of 100 model/tool iterations "
            "without receiving a final response."
        )


    def _route_aura_provider(self, llm_provider: str, llm: BaseChatModel) -> str:
        """Resolve provider aliases and validate Aura's supported providers.

        An explicitly supplied provider wins; otherwise the provider is inferred
        from the LLM instance. Claude aliases map to Anthropic, while Gemini and
        Vertex AI aliases map to Google.
        """

        provider = (llm_provider or self.detect_provider_from_llm(llm)).lower()
        if provider == "claude":
            provider = "anthropic"
        elif provider in {"gemini", "vertexai", "vertex_ai"}:
            provider = "google"

        if provider not in {"anthropic", "openai", "google"}:
            raise ValueError(
                "aura_invoker supports only Anthropic, OpenAI, and Gemini/Google. "
                f"Received {llm_provider!r}."
            )
        return provider


    @staticmethod
    def _is_context_length_error(exc: Exception) -> bool:
        value = str(exc).lower()
        return any(
            marker in value
            for marker in (
                "context_length_exceeded",
                "context length exceeded",
                "maximum context length",
                "input is too long",
                "too many tokens",
            )
        )


    @staticmethod
    def _build_aura_user_message(
        *,
        query: str,
        system_info: Optional[SystemInfo | str],
        attached_files: Optional[List[Dict[str, Any]]],
        attached_images: Optional[List[Dict[str, Any]]],
        base_64_image: Optional[List[str]],
        screenshot: Optional[Any],
    ) -> Dict[str, Any]:
        """Build Aura's canonical user message and all multimodal context.

        System information is converted to readable text before the shared
        message builder combines it with the query, current time, screenshots,
        uploaded images, text files, and PDF documents.
        """

        if isinstance(system_info, SystemInfo):
            system_info_text = (
                f"OS: {system_info.os}, Version: {system_info.version}, "
                f"Workspace: {system_info.workspace}, CWD: {system_info.cwd}"
            )
        elif system_info is None:
            system_info_text = None
        else:
            system_info_text = str(system_info)

        return build_user_message(
            query=query,
            attached_files=attached_files,
            attached_images=attached_images,
            base_64_images=base_64_image,
            screenshot=screenshot,
            system_info=system_info_text,
            today=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )


    @staticmethod
    def _normalize_aura_conversation(
        *,
        history: Optional[List[Dict[str, Any]]],
        user_message: Optional[Dict[str, Any]],
        provider: str,
        system_prompt: str,
    ) -> tuple[List[Dict[str, Any]], List[Any]]:
        """Normalize client history and format the conversation for a provider.

        Canonical messages remain provider-neutral for Aura's internal loop.
        Native messages are the equivalent Anthropic, OpenAI, or Gemini request
        representation used only at the provider boundary.
        """

        canonical_messages = normalize_history(history)
        if user_message is not None:
            canonical_messages.append(user_message)

        if provider == "anthropic":
            native_messages = anthropic_message_formater(canonical_messages)
        elif provider == "openai":
            native_messages = openai_message_formater(
                canonical_messages,
                system_prompt=system_prompt,
            )
        else:
            native_messages = gemini_message_formater(canonical_messages)
        return canonical_messages, native_messages


    @staticmethod
    def _format_aura_messages(
        *,
        provider: str,
        canonical_messages: List[Dict[str, Any]],
        system_prompt: str,
    ) -> List[Any]:
        """Rebuild provider messages after canonical context replacement."""

        if provider == "anthropic":
            return anthropic_message_formater(canonical_messages)
        if provider == "openai":
            return openai_message_formater(
                canonical_messages,
                system_prompt=system_prompt,
            )
        return gemini_message_formater(canonical_messages)


    async def _summarize_dedicated_context(
        self,
        compressor_input: str,
        *,
        service: AnthropicCompressionService,
        model: str,
        max_tokens: int,
        aggregate_usage: Dict[str, Any],
    ):
        """Call the standalone compressor service and account for its usage."""

        started_at = time.perf_counter()
        result = await service.summarize(
            compressor_input,
            model=model,
            max_output_tokens=max_tokens,
        )
        self._track_aura_usage_and_cost(
            provider=service.provider,
            model_name=model,
            parsed_response={
                "usage": {
                    "input": result.input_tokens,
                    "output": result.output_tokens,
                    "total_tokens": result.input_tokens + result.output_tokens,
                },
                "finish_reason": "end_turn",
                "tool_calls": [],
            },
            duration_ms=(time.perf_counter() - started_at) * 1000,
            aggregate_usage=aggregate_usage,
            credential_source="platform",
        )
        return result


    @staticmethod
    def _build_aura_tool_registry(
        tools: Optional[List[Tool]],
        provider: str,
    ) -> tuple[Dict[str, Tool], List[Dict[str, Any]]]:
        """Index Aura tools and format their schemas for the provider.

        The name-to-tool registry is used during execution. The native tool list
        exposes those same tools using the selected provider's request schema.
        """

        selected_tools = list(tools or [])
        tool_map = {
            str(getattr(tool, "name", "")): tool
            for tool in selected_tools
            if getattr(tool, "name", None)
        }

        if provider == "anthropic":
            native_tools = anthropic_tool_formater(selected_tools)
        elif provider == "openai":
            native_tools = openai_tool_formater(selected_tools)
        else:
            native_tools = gemini_tool_formater(selected_tools)
        return tool_map, native_tools


    @staticmethod
    async def _invoke_aura_model(
        *,
        provider: str,
        llm: BaseChatModel,
        model_name: str,
        system_prompt: str,
        native_messages: List[Any],
        native_tools: List[Dict[str, Any]],
        output_limit: int,
    ) -> Any:
        """Send one Aura request through the selected provider's native API."""

        if provider == "anthropic":
            return await invoke_anthropic_messages(
                llm=llm,
                model=model_name,
                system_prompt=system_prompt,
                messages=native_messages,
                tools=native_tools,
                max_tokens=output_limit,
            )
        if provider == "openai":
            return await invoke_openai_chat_completions(
                llm=llm,
                model=model_name,
                messages=native_messages,
                tools=native_tools,
                max_tokens=output_limit,
            )
        return await invoke_gemini_generate_content(
            llm=llm,
            model=model_name,
            system_prompt=system_prompt,
            contents=native_messages,
            tools=native_tools,
            max_tokens=output_limit,
        )


    @staticmethod
    def _parse_aura_response(provider: str, raw_response: Any) -> Dict[str, Any]:
        """Convert a native provider response into Aura's canonical response."""

        if provider == "anthropic":
            return anthropic_response_formater(raw_response)
        if provider == "openai":
            return openai_response_formater(raw_response)
        return gemini_response_formater(raw_response)


    def _track_aura_usage_and_cost(
        self,
        *,
        provider: str,
        model_name: str,
        parsed_response: Dict[str, Any],
        duration_ms: float,
        aggregate_usage: Dict[str, Any],
        credential_source: Optional[str] = None,
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """Calculate, persist, and aggregate usage, cost, and timing details.

        Assistant tool-call messages are also saved to memory before tool
        execution, preserving the behavior expected by client-side tools.
        """

        usage = dict(parsed_response.get("usage") or {})
        input_tokens = int(usage.get("input") or 0)
        output_tokens = int(usage.get("output") or 0)
        usage["total_tokens"] = int(
            usage.get("total_tokens") or input_tokens + output_tokens
        )
        usage["cost"] = calculate_token_cost_usd_float(
            provider=provider,
            model_name=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        details = {
            "provider": provider,
            "model_name": model_name,
            "credential_source": credential_source or self.credential_source,
            "finish_reason": parsed_response.get("finish_reason"),
            "llm_duration_ms": round(duration_ms, 2),
        }

        schedule_token_usage_update(
            pool=self.rate_limit_pool,
            user_id=self.user_id,
            usage=usage,
            details=details,
            event_loop=self.rate_limit_loop,
        )

        tool_calls = parsed_response.get("tool_calls") or []
        if tool_calls:
            update_memory(
                role="assistant",
                content=parsed_response.get("text") or "",
                memory=self.memory,
                tool_calls=tool_calls,
                usage=usage,
                details=details,
            )

        for key in ("input", "output", "total_tokens"):
            aggregate_usage[key] += int(usage.get(key) or 0)
        aggregate_usage["cost"] += float(usage.get("cost") or 0.0)
        return usage, details


    @staticmethod
    def _empty_aura_usage() -> Dict[str, Any]:
        """Create the usage accumulator for one Aura invocation."""

        return {"input": 0, "output": 0, "total_tokens": 0, "cost": 0.0}


    @staticmethod
    async def _execute_native_tool_call(
        tool_call: Dict[str, Any],
        tool_map: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Execute one native tool request and return a canonical tool result.

        Passing a LangChain ToolCall object preserves ``tool_call_id`` inside
        AuraStructuredTool, allowing client-side tools to route websocket
        responses correctly. Unknown tools and execution failures are returned
        to the model as structured errors instead of ending the Aura loop.
        """

        tool = tool_map.get(str(tool_call.get("name") or ""))
        if tool is None:
            return canonical_tool_result(
                tool_call=tool_call,
                error=ValueError(f"Unknown tool requested: {tool_call.get('name')!r}"),
            )

        invocation = {
            "name": tool_call.get("name"),
            "args": tool_call.get("input") or {},
            "id": tool_call.get("tool_call_id"),
            "type": "tool_call",
        }
        try:
            result = await tool.ainvoke(invocation)
            return canonical_tool_result(tool_call=tool_call, result=result)
        except Exception as exc:
            return canonical_tool_result(tool_call=tool_call, error=exc)


    @staticmethod
    def _record_aura_tool_exchange(
        *,
        provider: str,
        parsed_response: Dict[str, Any],
        tool_calls: List[Dict[str, Any]],
        tool_results: List[Dict[str, Any]],
        canonical_messages: Optional[List[Dict[str, Any]]],
        native_messages: List[Any],
        generated_messages: List[Dict[str, Any]],
        intermediate_steps: List[Dict[str, Any]],
    ) -> None:
        """Add completed tool calls and results to Aura's conversation state.

        The native assistant message is retained exactly so Anthropic thinking
        blocks and Gemini thought signatures survive the next model iteration.
        """

        if canonical_messages is not None:
            canonical_messages.extend(tool_results)
        generated_messages.extend(tool_results)
        intermediate_steps.extend(
            {
                "tool_call": tool_call,
                "tool_result": tool_result,
            }
            for tool_call, tool_result in zip(tool_calls, tool_results)
        )

        native_messages.append(parsed_response["native_message"])
        if provider == "anthropic":
            native_messages.extend(anthropic_message_formater(tool_results))
        elif provider == "openai":
            native_messages.extend(openai_message_formater(tool_results))
        else:
            native_messages.extend(
                gemini_tool_result_formater(
                    tool_results,
                    parsed_response.get("native_tool_call_ids"),
                )
            )


    @staticmethod
    def _build_aura_result(
        *,
        parsed_response: Dict[str, Any],
        generated_messages: List[Dict[str, Any]],
        intermediate_steps: List[Dict[str, Any]],
        aggregate_usage: Dict[str, Any],
        provider: str,
        model_name: str,
        iterations: int,
        agent_type: AGENT_TYPE,
    ) -> Dict[str, Any]:
        """Build the stable response returned after Aura produces final text."""

        aggregate_usage["cost"] = round(aggregate_usage["cost"], 6)
        return {
            "output": parsed_response.get("text") or "",
            "messages": generated_messages,
            "intermediate_steps": intermediate_steps,
            "usage": aggregate_usage,
            "provider": provider,
            "model": model_name,
            "iterations": iterations,
            "agent_type": agent_type,
        }


    async def aura_executor(
            self,
            system_prompt: str,
            llm: BaseChatModel,
            query: str,
            llm_provider: str,
            agent_type: AGENT_TYPE,
            system_info: Optional[SystemInfo | str] = None,
            tools: Optional[List[Tool]] = None,
            chat_history: Optional[List[Message]] = None,
            base_64_image:  Optional[List[str]] = None,
            attached_files: Optional[List[Dict[str, Any]]] = None,
            attached_images: Optional[List[Dict[str, Any]]] = None,
            max_tokens: int = 128000,
            history: List[Dict] = [],
            screenshot: Optional[Any] = None,
        ):
        """
        Method to run the aura agent with the given task in a loop till the task is not completed.

        inputs:
            - system_prompt: Initial system prompt to guide the agent.
            - llm: The language model to use.
            - query: The user's question or command.
            - system_info: Optional system-specific information.
            - tools: Optional list of tools the agent can use.
            - chat_history: Optional list of previous messages to maintain continuity.
            - base_64_image: Optional image input in base64 or URL.
            - max_tokens: Maximum token limit for the LLM response.
        
        Returns:
            - The response from the agent or LLM after processing the query.
        """
        try:
            # Prepare chat history from memory and provided history
            chat_history_for_llm = []
            if history:
                provider = self.detect_provider_from_llm(llm)
                # Debug: log history shape to detect malformed entries
                print(f"[aura_executor] History length: {len(history)}, entry types: {[type(m).__name__ for m in history[:5]]}")
                chat_history_for_llm.extend(format_to_langchain(history, provider=provider))
            
            # Prepare system prompt
            if system_prompt:
                prompt = ChatPromptTemplate.from_messages([
                    ("system", system_prompt),
                    MessagesPlaceholder(variable_name="chat_history"),
                    MessagesPlaceholder(variable_name="input"),
                    MessagesPlaceholder(variable_name="agent_scratchpad")
                ])
            
            system_info_str = system_info
            if system_info and isinstance(system_info, SystemInfo):
                system_info_str = f"OS: {system_info.os}\n, Version: {system_info.version}\n, Workspace: {system_info.workspace}\n, CWD: {system_info.cwd}\n"
            
            today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # Format input using prepareMessageForAI
            formated_input = prepareMessageForAI(
                llm_provider=llm_provider,
                attached_files=attached_files or [],
                attached_images=attached_images or [],
                query=query,
                screenshot=screenshot
            )

            # If formated_input is a list (multimodal), we append system_info and today to the first text block if possible
            # or just add another text block.
            system_info_text = f"\n\nsystem_info: {system_info_str}\ntoday: {today}\n"
            
            if isinstance(formated_input, list):
                # Find the query block and append system_info and today to it
                for block in formated_input:
                    if block.get("type") == "text" and block.get("text", "").startswith("query:"):
                        block["text"] += f"\nsystem_info: {system_info_str}\ntoday: {today}\n"
                        break
            else:
                # It's a string
                formated_input += system_info_text

            # Create callbacks list with rate limiting
            handler = AgentCallbackHandler(
                self.memory,
                rate_limit_pool=self.rate_limit_pool,
                user_id=self.user_id,
                rate_limit_loop=self.rate_limit_loop,
                fallback_provider=self.fallback_provider,
                fallback_model_name=self.fallback_model_name,
                fallback_credential_source=self.credential_source,
            )
            # callbacks = handler.as_list()

            # llm_with_callbacks = llm.with_config({"callbacks": callbacks})  # ← key line

            agent = create_tool_calling_agent(
                llm,
                tools,
                prompt,
                message_formatter=lambda steps: format_multimodal_tool_messages(
                    steps,
                    provider=llm_provider,
                ),
            )

            # Creating agent executor.
            executor = AgentExecutor(
                agent=agent,
                tools=tools,
                verbose=True,
                return_intermediate_steps=True,
                max_iterations=100,
                early_stopping_method="generate"
            )

            # Invoking LLM
            # Wrap formated_input in a HumanMessage for proper multimodal support
            input_message = HumanMessage(content=formated_input)

            # Invoking LLM
            response = await executor.ainvoke(
                {"input": [input_message], "chat_history": chat_history_for_llm},
                config=RunnableConfig(callbacks=handler.as_list()),
            )
            # returning response bac to the aura agent.
            return response

        except MaxOutputTokenLimitError:
            raise
        except Exception as e:
            raise RuntimeError(f"Failed to execute agent or LLM call: {str(e)}")


    # def decide_source(self, llm: BaseChatModel, query: str, page_content: str) -> Dict[str, Any]:
    #     system_content = CLASSIFIER_PROMPT.format(page_content=page_content)
    #     messages = [
    #         SystemMessage(content=system_content),
    #         HumanMessage(content=query),
    #     ]
    #     response = llm.invoke(messages)
    #     try:
    #         return json.loads(response.content)
    #     except json.JSONDecodeError as exc:
    #         raise ValueError(f"Classifier returned invalid JSON: {response.content}") from exc


    def create_agent_for_provider(
        self,
        llm: BaseChatModel,
        prompt: ChatPromptTemplate,
        tools: List[Tool],
        llm_provider: Optional[str] = None
    ):
        """
        Creates an appropriate agent based on the LLM provider.

        Args:
            llm: The language model instance
            prompt: The chat prompt template
            tools: List of tools available to the agent
            llm_provider: Name of the LLM provider

        Returns:
            Agent instance compatible with the specified provider
        """
        # Detect provider from LLM class name if not explicitly provided
        if not llm_provider:
            llm_provider = self.detect_provider_from_llm(llm)

        llm_provider = llm_provider.lower()

        # Create agent based on provider
        if llm_provider in ['openai', 'azure_openai', 'azure', 'open_router']:
            return create_tool_calling_agent(
                llm,
                tools,
                prompt,
                message_formatter=lambda steps: format_multimodal_tool_messages(
                    steps,
                    provider=llm_provider,
                ),
            )
    
        elif llm_provider in ['anthropic', 'claude']:
            # Anthropic supports tool calling through the generic create_tool_calling_agent
            # Ensure the LLM is bound with tools properly for Anthropic
            return create_tool_calling_agent(
                llm,
                tools,
                prompt,
                message_formatter=lambda steps: format_multimodal_tool_messages(
                    steps,
                    provider=llm_provider,
                ),
            )
    
        elif llm_provider in ['google', 'gemini', 'vertexai', 'vertex_ai']:
            # Google/Gemini also supports the generic tool calling agent
            return create_tool_calling_agent(
                llm,
                tools,
                prompt,
                message_formatter=lambda steps: format_multimodal_tool_messages(
                    steps,
                    provider=llm_provider,
                ),
            )
    
        elif llm_provider in ['cohere']:
            # Cohere supports tool calling
            return create_tool_calling_agent(
                llm,
                tools,
                prompt,
                message_formatter=lambda steps: format_multimodal_tool_messages(
                    steps,
                    provider=llm_provider,
                ),
            )
    
        elif llm_provider in ['mistral']:
            # Mistral AI supports tool calling
            return create_tool_calling_agent(
                llm,
                tools,
                prompt,
                message_formatter=lambda steps: format_multimodal_tool_messages(
                    steps,
                    provider=llm_provider,
                ),
            )
    
        else:
            # Default to generic tool calling agent for other providers
            # This should work for most modern LLMs that support function calling
            try:
                return create_tool_calling_agent(
                    llm,
                    tools,
                    prompt,
                    message_formatter=lambda steps: format_multimodal_tool_messages(
                        steps,
                        provider=llm_provider,
                    ),
                )
            except Exception as e:
                raise ValueError(
                    f"Unsupported LLM provider: {llm_provider}. "
                    f"Provider does not support tool calling or is not recognized. "
                    f"Error: {str(e)}"
                )
    
    def detect_provider_from_llm(self, llm: BaseChatModel) -> str:
        """
        Attempts to detect the LLM provider from the LLM instance class name.

        Args:
            llm: The language model instance

        Returns:
            Detected provider name as a string
        """
        llm_class_name = llm.__class__.__name__.lower()

        if 'openai' in llm_class_name or 'azurechatopenai' in llm_class_name or 'openrouter' in llm_class_name:
            return 'openai'
        elif 'anthropic' in llm_class_name or 'claude' in llm_class_name:
            return 'anthropic'
        elif 'google' in llm_class_name or 'gemini' in llm_class_name or 'vertex' in llm_class_name:
            return 'google'
        elif 'cohere' in llm_class_name:
            return 'cohere'
        elif 'mistral' in llm_class_name:
            return 'mistral'
        else:
            # Default to generic
            return 'generic'
