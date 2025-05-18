from typing import Optional, List, Union, Dict, Any
from langchain_openai.chat_models.base import ChatOpenAI
from langchain_community.chat_models.anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain.agents import create_openai_tools_agent, create_tool_calling_agent, AgentExecutor
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import Runnable
from langchain.tools import Tool
from langchain.output_parsers import PydanticOutputParser
from langchain_core.runnables import RunnableLambda
import re
import json


from app.Types.agent_types import LLMConfig, StepsList, SystemInfo, AGENT_TYPE
from app.LLM.memory import Message
from app.LLM.memory import Memory
from app.helper import update_memory

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
    def create_llm(llm_config: LLMConfig):
        """
        Creates a language model instance based on the given provider and model name.

        Input:
        - llm_config: Configuration containing provider and model_name.

        Returns:
        - An instance of ChatOpenAI or ChatAnthropic.
        """
        try:
            if llm_config.provider == "openai":
                return ChatOpenAI(model=llm_config.model_name)
            elif llm_config.provider == "anthropic":
                return ChatAnthropic(model=llm_config.model_name)
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
    
    def get_multimodal_query(self, query: str, base64_image: str, current_state: Optional[str] = None):
        """
        Formats a multimodal query combining user text and screenshot data.

        Input:
        - query: User's textual input.
        - screenshot: A base64 image string or URL.

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
        if base64_image:
            multimodal_query.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{base64_image}"
                }
            })

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
        screenshot: Optional[str] = None,
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
        - max_tokens: Maximum token limit for the LLM response.

        Returns:
        - The response from the agent or LLM after processing the query.
        """
        try:
            if screenshot:
                multimodal_query = self.get_multimodal_query(query=query, base64_image=screenshot)
            else:
                multimodal_query = query

                # Add the user's query in the memory -> (User query added in Agent class)
                # user_query = Message.user_message(content=query, base64_image=screenshot)
                # self.memory.add_message(user_query)


            # Prepare chat history
            if chat_history:
                chat_history_for_llm = [message.to_dict() for message in chat_history]

            update_memory("user", content=query, base64_image=screenshot, memory=self.memory)
            
            # Prepare system prompt
            if system_prompt:
                prompt = ChatPromptTemplate.from_messages([
                    ("system", system_prompt),
                    MessagesPlaceholder(variable_name="chat_history"),
                    ("human", "{input}"),
                    MessagesPlaceholder(variable_name="agent_scratchpad")
                ])
        
            
            system_info_str = system_info
            if system_info and isinstance(system_info, SystemInfo):
                system_info_str = f"OS: {system_info.os}, Version: {system_info.version}"

            
            formated_input = (
                f"query: {multimodal_query}\n"
                f"system_info: {system_info_str}\n"
            )
            
            # If tools are provided, use create_openai_tools_agent
            if tools:
                # agent = self.get_agent_type(llm=llm, prompt=prompt, tools=tools)
                agent = create_openai_tools_agent(llm, tools, prompt)

                executor = AgentExecutor(
                    agent=agent,
                    tools=tools,
                    verbose=True,
                    return_intermediate_steps=True
                )

                response = await executor.ainvoke({"input": formated_input, "chat_history": chat_history_for_llm})
                
                return response
            else:
                # Invoke the LLM
                response = await llm.ainvoke({"input": formated_input})

                # adding response to memory as assistant message.
                assistant_message = Message.assistant_message(content=response.content, base64_image=screenshot)
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
        system_prompt: str,
        query: str,
        agent_type: AGENT_TYPE,  # type: ignore
        chat_history: List[Message],
        system_info: Optional[SystemInfo | str] = None,
        base64_image: Optional[str] = None,
        current_state: Optional[str] = None,  #(parsed_page)
        max_tokens = 128000,
    ):
        try: 
            if base64_image:
                query = self.get_multimodal_query(query, base64_image, current_state)

            chat_history_for_llm = []
            if chat_history:
                chat_history_for_llm = [message.to_dict() for message in chat_history]
            
            
            if system_prompt:
                prompt = ChatPromptTemplate.from_messages([
                    ("system", system_prompt),  # Only include system prompt #WIP** need to add the parsed_page
                    ("system", "{system_info}"),
                    MessagesPlaceholder(variable_name="chat_history"),
                    ("human", "{query}"),
                    # MessagesPlaceholder(variable_name="agent_scratchpad")
                ])
            
            input_message = prompt.format_messages(
                system_prompt = system_prompt,
                system_info = system_info,
                chat_history = chat_history_for_llm,
                query = query
            )
            
            response = await llm.ainvoke(input_message)

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
            print("Parsed JSON:", data_dict)
            return data_dict
        except json.JSONDecodeError as e:
            print("Failed to parse JSON:", e)
            return None