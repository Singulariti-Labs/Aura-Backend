from typing import Optional, Dict, List, Any
from langchain_core.language_models.chat_models import BaseChatModel
import json

from app.LLM.memory import Memory, Message
from app.Types.agent_types import SystemInfo, LLMConfig, StepStatus, ROLE_TYPE
from app.helper import update_memory
from app.Prompts.interaction import INTERACTION_AGENT_PROMPT
from app.LLM.llm_factory import LLMFactory



class InteractionAgent():

    def __init__(self,  llm: BaseChatModel, memory: Optional[Memory] = None, maxTokens: int = 128000) -> None:
        self.llm = llm;
        self.max_tokens = maxTokens
        self.system_info = None
        self.shared_memory = memory
        self.subagent_memory = Memory()
        self.interaction_agent_prompt = INTERACTION_AGENT_PROMPT
        self.max_failure = 4
        self.max_steps = 100
        self.max_actions_per_step = 10
        self.failure_count = 0
        self.llm_factory = LLMFactory(self.subagent_memory)
        

    async def invoke(self, query: str, system_info: Optional[SystemInfo] = None, screenshot: Optional[str] = None ):
        """"It invokes the Interaction Agent"""
        try:
            current_state_screenshot = await "API call (get base64 string)" #WIP***

            self.system_info = system_info
            
            prev_context = self.shared_memory.messages    #prev context from other agents ie, shared memory.

            # update_memory(role="user", content=query, base64_image=current_state_screenshot, memory=self.subagent_memory)

            self.subagent_memory.add_messages(prev_context)

            interaction_agent_memory = self.subagent_memory.messages

            response = await self.think(
                query=query,
                chat_history=interaction_agent_memory,
                base64_image=current_state_screenshot
            )

            return response

        except Exception as e:
            raise RuntimeError(f"Interactiion Agent Invoke Failed: {e}")

    async def think(self, query: str, chat_history: List[Message], base64_image: str):
        """Process invokes the LLM for the react style Interaction agent"""

        for step in range(self.max_steps):
            try:
                if await self.is_terminate():
                    #LOGGER: log the termination response because of max_failer
                    return {"status": "terminated", "reason": "Terminating due to max failure"}
                
                
                result = await self.llm_factory.invoke_interaction_agent(
                    query=query,
                    base64_image=base64_image,
                    chat_history=chat_history,
                    llm=self.llm,
                    agent_type = "interaction",
                    system_info= self.system_info,
                    system_prompt= self.interaction_agent_prompt
                )

                update_memory(role="user", content=query, base64_image=base64_image, memory=self.subagent_memory)
                update_memory(role="assistant", content=json.dumps(result))

                if await self.is_task_completed(result):
                    update_memory(role="tool", content=json.dumps(result), name="interaction", base64_image=base64_image)
                    break



            except Exception as e:
                raise RuntimeError(f"Failed during Think process in Interaction Agent: {e}")
                

    
    async def is_terminate(self) -> bool:
        # Example termination logic — expand as needed
        return self.failure_count >= self.max_failure
    
    def is_task_completed(data: dict) -> bool:
        """
        Checks if the last action step has 'is_done' key to determine task completion.
        
        Parameters:
            data (dict): The JSON dictionary containing the action steps.
        
        Returns:
            bool: True if task is marked as done, False otherwise.
        """
        actions = data.get("action", [])
        if not actions:
            return False

        last_action = actions[-1]
        return "is_done" in last_action



            
                 
             


        
