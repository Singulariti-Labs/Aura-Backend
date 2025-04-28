from typing import Optional, List, Union, Dict, Any
from langchain.chat_models import ChatOpenAI, ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain.agents import create_openai_tools_agent, create_tool_calling_agent, AgentExecutor
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import Runnable
from langchain.tools import Tool
from langchain.output_parsers import PydanticOutputParser


from app.Types.agent_types import LLMConfig, StepsList
from app.LLM.memory import Message
from app.LLM.memory import Memory

class LLMFactory:

    memory: Memory

    @staticmethod
    def create_llm(llm_config: LLMConfig) -> BaseChatModel:
        try:
            if llm_config.provider == "openai":
                return ChatOpenAI(model=llm_config.model_name)
            elif llm_config.provider == "anthropic":
                return ChatAnthropic(model=llm_config.model_name)
            else:
                raise ValueError(f"Unsupported provider: {llm_config.provider}")
        except Exception as e:
            raise RuntimeError(
                f"Error creating LLM instance for provider '{llm_config.provider}' "
                f"with model '{llm_config.model_name}': {str(e)}"
            )
    
    def get_agent_type(llm: BaseChatModel, prompt: ChatPromptTemplate, tools: Optional[List[Tool]] = None):

        if isinstance(llm, ChatOpenAI):
            return create_openai_tools_agent(llm, prompt, tools)
        elif isinstance(llm, ChatAnthropic):
            return create_tool_calling_agent(llm, prompt, tools)
        else:
            raise ValueError(f"Unsupported LLM type: {type(llm)}")
    
    def invoke_agent(
        llm:BaseChatModel,
        agent: Runnable,
        query: Union[str, List[Dict[str, Union[str, dict]]]],
        chat_history: Optional[List[Message]] = []
    ):
        agent_executor = AgentExecutor(
            agent=agent,
            verbose=True,
            return_intermediate_steps=True
        )
        result = agent_executor.invoke({"input": query, "chat_history": chat_history})
        return result
    
    def get_multimodal_query(query: str, screenshot: str):
        """Provide the input query format with screenshot"""

        multimodal_query =  [
                    {"type": "text", "text": query},
                    {
                        "type": "image_url", 
                        "image_url": {
                            "url": screenshot if screenshot.startswith("http") or screenshot.startswith("data:") 
                                else f"data:image/jpeg;base64,{screenshot}"
                        }
                    }
                ]
        return multimodal_query

    
    async def agent_executor( #WIP
        self,
        system_prompt: str,
        llm: BaseChatModel,
        query: str,
        tools: Optional[List[Tool]] = None,
        # chat_history: Optional[List[Union[HumanMessage, AIMessage, SystemMessage]]] = None,
        chat_history: Optional[List[Message]] = None,
        screenshot: Optional[str] = None,
        max_tokens: int = 128000,
    ):
        """This method is to invoke the AI agent with tools/subagent, chat_history and image
        """
        try:
            if screenshot:
                multimodal_query = self.get_multimodal_query(query, screenshot)
            else:
                multimodal_query = query

                # Add the user's query in the memory
                user_query = Message.user_message(content=query, base64_image=screenshot)
                self.memory.add_message(user_query)


            # Prepare chat history
            chat_history_for_llm = []
            if chat_history:
                chat_history_for_llm = [message.to_dict() for message in chat_history]
            
            # Prepare system prompt
            if system_prompt:
                prompt = ChatPromptTemplate.from_messages([
                    ("system", system_prompt),  # Only include system prompt here
                    MessagesPlaceholder(variable_name="chat_history_for_llm"),
                    MessagesPlaceholder(variable_name="agent_scratchpad"),
                    ("human", "{input}")
                ])
            
            # If tools are provided, use create_openai_tools_agent
            if tools:
                agent = self.get_agent_type(llm, prompt, tools)
                response = self.invoke_agent(llm, agent, multimodal_query, chat_history_for_llm) #WIP add the message to the memory from tool
                return response
            else:
                # Invoke the LLM
                response = await llm.ainvoke({"input": multimodal_query, "chat_history_for_llm": chat_history_for_llm})

                # adding response to memory as assistant message.
                assistant_message = Message.assistant_message(content=response.content, base64_image=screenshot)
                self.memory.add_message(assistant_message);
                return response
        
        except Exception as e:
            raise RuntimeError(f"Failed to execute agent or LLM call: {str(e)}")
            

    async def invoke_planner_agent(llm: BaseChatModel, prompt_template: str, query: str) -> List[Dict[str, str]]:
        """
        Invokes the planner agent using the provided LLM, prompt template, and user query.

        Args:
            llm: The language model instance to use (must have a .predict method).
            prompt_template: The template string with placeholders like {query} and {format_instructions}.
            query: The user query string that needs to be broken into steps.

        Returns:
            A list of dictionaries with keys: "id", "description", "thought", "dependency", and "expected_output".
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
