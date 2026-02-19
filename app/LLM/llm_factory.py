from dotenv import load_dotenv
from typing import Optional, List, Union, Dict, Any
from langchain_openai.chat_models.base import ChatOpenAI
from langchain_community.chat_models.anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_classic.agents import create_openai_tools_agent, create_tool_calling_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import Runnable
from langchain_core.tools import Tool
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.runnables import RunnableLambda
from langchain_core.messages import HumanMessage, SystemMessage
import os
import re
import json


from app.Types.agent_types import LLMConfig, StepsList, SystemInfo, AGENT_TYPE
from app.LLM.memory import Message
from app.LLM.memory import Memory
from app.helper import update_memory, update_input_messages_with_screenshot_and_context
from app.Prompts.validator import VALIDATOR_PROMPT
from app.Prompts.classifier_prompt import CLASSIFIER_PROMPT
from app.handler import AgentCallbackHandler
from datetime import datetime


load_dotenv()

class LLMFactory():
    """
    LLMFactory handles creation and execution of language model agents with optional tools, multimodal inputs, 
    and memory support for chat history.
    """

    def __init__(self, memory: Memory):
        """
        Initializes the LLMFactory with a memory instance to track chat history and message flow.

        Input:
        - memory: An instance of the Memory class to persist user and assistant messages.
        """
        self.memory = memory

    @staticmethod
    def create_llm(llm_config: LLMConfig, user_api_key: str = None):
        """
        Creates a language model instance based on the given provider and model name.

        Input:
        - llm_config: Configuration containing provider and model_name.

        Returns:
        - An instance of ChatOpenAI or ChatAnthropic.
        """
        try:
            if llm_config.provider == "openai":
                api_key = user_api_key or os.environ.get("OPENAI_API_KEY")
                
                if not api_key:
                    raise ValueError("OPENAI_API_KEY environment variable is not set")

                return ChatOpenAI(model=llm_config.model_name, api_key=api_key)
            
            elif llm_config.provider == "anthropic":
                api_key = user_api_key or os.environ.get("ANTHROPIC_API_KEY")
                
                if not api_key:
                    raise ValueError("ANTHROPIC_API_KEY environment variable is not set")
                
                return ChatAnthropic(model=llm_config.model_name)
            
            elif llm_config.provider == "open_router":
                api_key = user_api_key or os.environ.get("OPENROUTER_API_KEY")
                
                if not api_key:
                    raise ValueError("OPENROUTER_API_KEY environment variable is not set")
                if(llm_config.model_name == "z-ai"):
                    return ChatOpenAI(model="z-ai/glm-4.5-air:free", api_key=api_key, base_url="https://openrouter.ai/api/v1")
                if(llm_config.model_name == "x-ai"):
                    return ChatOpenAI(model="x-ai/grok-4.1-fast:free", api_key=api_key, base_url="https://openrouter.ai/api/v1")
                if(llm_config.model_name == "openai"):
                    return ChatOpenAI(model="openai/gpt-oss-120b:free", api_key=api_key, base_url="https://openrouter.ai/api/v1")
                if(llm_config.model_name == "xiaomi"):
                    return ChatOpenAI(model="xiaomi/mimo-v2-flash:free", api_key=api_key, base_url="https://openrouter.ai/api/v1")
                if(llm_config.model_name == "google"):
                    return ChatOpenAI(model="google/gemini-2.0-flash-exp:free", api_key=api_key, base_url="https://openrouter.ai/api/v1")
                if(llm_config.model_name == "qwen"):
                    return ChatOpenAI(model="qwen/qwen3-next-80b-a3b-instruct:free", api_key=api_key, base_url="https://openrouter.ai/api/v1")
                if(llm_config.model_name == "nvidia"):
                    return ChatOpenAI(model="nvidia/nemotron-3-nano-30b-a3b:free", api_key=api_key, base_url="https://openrouter.ai/api/v1")
                if(llm_config.model_name == "upstage"):
                    return ChatOpenAI(model="upstage/solar-pro-3:free", api_key=api_key, base_url="https://openrouter.ai/api/v1")
            
            elif llm_config.provider == "google":
                api_key = user_api_key or os.environ.get("GOOGLE_API_KEY")
                
                if not api_key:
                    raise ValueError("GOOGLE_API_KEY environment variable is not set")
                
                return ChatGoogleGenerativeAI(model=llm_config.model_name, api_key=api_key)
            
            elif llm_config.provider == "agent_router":
                api_key = user_api_key or os.environ.get("AGENTROUTER_API_KEY")
                
                if not api_key:
                    raise ValueError("AGENT_ROUTER_API_KEY environment variable is not set")

                return ChatOpenAI(model=llm_config.model_name, api_key=api_key, base_url="https://api.agentrouter.com/v1")
            
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
            return create_openai_tools_agent(llm, prompt, tools)
        elif isinstance(llm, ChatAnthropic):
            return create_tool_calling_agent(llm, prompt, tools)
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
            
            # If tools are provided, use create_openai_tools_agent
            if tools:
                # agent = self.get_agent_type(llm=llm, prompt=prompt, tools=tools)
                agent = self.create_agent_for_provider(
                    llm=llm,
                    prompt=prompt,
                    tools=tools,
                    llm_provider=llm_provider
                )

                executor = AgentExecutor(
                    agent=agent,
                    tools=tools,
                    verbose=True,
                    return_intermediate_steps=True,
                    callbacks=[AgentCallbackHandler(self.memory)]
                )

                response = await executor.ainvoke({"input": formated_input, "chat_history": chat_history_for_llm})
                
                return response
            else:
                # Invoke the LLM
                response = await llm.ainvoke({"input": formated_input})

                # adding response to memory as assistant message.
                assistant_message = Message.assistant_message(content=response.content)
                self.memory.add_message(assistant_message);
                return response
        
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


    async def aura_executor(
            self,
            system_prompt: str,
            llm: BaseChatModel,
            query: str,
            agent_type: AGENT_TYPE,
            system_info: Optional[SystemInfo | str] = None,
            tools: Optional[List[Tool]] = None,
            chat_history: Optional[List[Message]] = None,
            base_64_image:  Optional[List[str]] = None,
            max_tokens: int = 128000,
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
            # Prepare chat history  WIP**- Adding chat_histroy later when we introduce memory for prev_message.
            # if chat_history:
            #     chat_history_for_llm = [message.to_dict() for message in chat_history]
            
            # Prepare system prompt
            if system_prompt:
                prompt = ChatPromptTemplate.from_messages([
                    ("system", system_prompt),
                    ("human", "{input}"),
                    MessagesPlaceholder(variable_name="agent_scratchpad")
                ])
            
            system_info_str = system_info
            if system_info and isinstance(system_info, SystemInfo):
                system_info_str = f"OS: {system_info.os}, Version: {system_info.version}, Workspace: {system_info.workspace}, CWD: {system_info.cwd}"

            today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            formated_input = (
                f"query: {query}\n"
                f"system_info: {system_info_str}\n"
                f"today: {today}\n"
            )
            
            
            agent = create_openai_tools_agent(llm, tools, prompt)

            # Create callbacks list with rate limiting
            callbacks = [
                AgentCallbackHandler(self.memory),
            ]

            # Creating agent executor.
            executor = AgentExecutor(
                agent=agent,
                tools=tools,
                verbose=True,
                return_intermediate_steps=True,
                callbacks=callbacks,
                max_iterations=100,
                early_stopping_method="generate"
            )

            # Invoking LLM
            response = await executor.ainvoke({"input": formated_input})
            # returning response bac to the aura agent.
            return response
                

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
            # OpenAI-specific tool calling agent
            return create_openai_tools_agent(llm, tools, prompt)
    
        elif llm_provider in ['anthropic', 'claude']:
            # Anthropic supports tool calling through the generic create_tool_calling_agent
            # Ensure the LLM is bound with tools properly for Anthropic
            return create_tool_calling_agent(llm, tools, prompt)
    
        elif llm_provider in ['google', 'gemini', 'vertexai', 'vertex_ai']:
            # Google/Gemini also supports the generic tool calling agent
            return create_tool_calling_agent(llm, tools, prompt)
    
        elif llm_provider in ['cohere']:
            # Cohere supports tool calling
            return create_tool_calling_agent(llm, tools, prompt)
    
        elif llm_provider in ['mistral']:
            # Mistral AI supports tool calling
            return create_tool_calling_agent(llm, tools, prompt)
    
        else:
            # Default to generic tool calling agent for other providers
            # This should work for most modern LLMs that support function calling
            try:
                return create_tool_calling_agent(llm, tools, prompt)
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