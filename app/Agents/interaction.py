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
        self.interaction_tool_id = None
        

    async def invoke(self, query: str, tool_call_id: str, system_info: Optional[SystemInfo | str] = None, screenshot: Optional[str] = None ):
        """ Invokes the Interaction Agent which performs interaction on device on the behalf of user.

        Input:
            query (str): The user's input or task description to be processed by the agent.
            tool_call_id (str): An identifier used to track the specific tool call for logging or coordination.
            system_info (Optional[SystemInfo | str]): Information about the current system environment (OS, apps, etc.).
            screenshot (Optional[str]): A base64-encoded screenshot representing the current state of the system UI (optional).
        """
        try:
            self.system_info = system_info
            self.interaction_tool_id = tool_call_id
            base64_image = screenshot # Get the screenshot from client of curret_state
            self.subagent_memory.add_messages(self.shared_memory.messages)

            response = await self.think(query, self.subagent_memory.messages, base64_image)
            status = response.get("status")

            if status == "success":
                return f"[✅ Success] Step {response.get('step')}: {response.get('result')}"
            if status == "incomplete":
                return f"[⚠️ Incomplete] {response.get('reason')} | Last result: {response.get('last_result')}"
            if status == "failed":
                return f"[❌ Failed] Step {response.get('step')}: {response.get('reason')}"

            return "[❓ Unknown] Unexpected agent status."

        except Exception as e:
            return f"[💥 Error] Interaction Agent Invoke Failed: {str(e)}"


    async def think(self, query: str, chat_history: List[Message], base64_image: str):
        """ Core reasoning loop for the Interaction Agent.

        This method runs a ReAct-style agent loop for a maximum number of steps (`self.max_steps`), 
        invoking the LLM at each step to process the query and generate the response.

        Input:
            query (str): The user's original request or task instruction.
            chat_history (List[Message]): The ongoing chat memory to provide full context to the LLM.
            base64_image (str): A base64-encoded screenshot representing the current UI or system state.
        """

        last_result = None  # To store the latest result from the agent

        for step in range(self.max_steps):
            try:
                # GET_MESSAGE_FROM_CLIENT - current screenshot.

                if await self.is_terminate():
                    # Logging can be added here if needed
                    return {
                        "status": "failed",
                        "reason": "Terminating due to max failure or explicit termination signal",
                        "result": None
                    }

                result = await self.llm_factory.invoke_interaction_agent(
                    query=query,
                    base64_image=base64_image,
                    chat_history=chat_history,
                    llm=self.llm,
                    agent_type="interaction",
                    system_info=self.system_info,
                    system_prompt=self.interaction_agent_prompt
                )

                last_result = result  # Save the latest result in case we need to return it later

                # Update local memory with the conversation
                update_memory(role="user", content=query, base64_image=base64_image, memory=self.subagent_memory)
                update_memory(role="assistant", content=json.dumps(result), memory=self.subagent_memory)

                # SEND_RESPONSE_TO_CLINET - Interaction agent output
                
                # Check if task has been completed
                if await self.is_task_completed(result):
                    update_memory(
                        role="tool",
                        content=json.dumps(result),
                        name="interaction",
                        tool_call_id=self.interaction_tool_id,
                        base64_image=base64_image,
                        memory=self.shared_memory
                    )
                    return {
                        "status": "success",
                        "result": result,
                        "step": step + 1
                    }

            except Exception as e:
                # Optional: Add logging here
                return {
                    "status": "failed",
                    "step": step + 1,
                    "reason": f"Exception occurred: {str(e)}",
                    "result": None
                }

        # If max steps are exhausted but task not marked complete
        return {
            "status": "incomplete",
            "reason": "Maximum steps reached without completing the task",
            "last_result": last_result,
            "result": None
        }
                

    
    async def is_terminate(self) -> bool:
        """ Check if the faliure_count is greated than max_failure count"""
        return self.failure_count >= self.max_failure
    
    async def is_task_completed(self, data: dict) -> bool:
        """
        Checks if the last action step has 'is_done' key to determine task completion.
        
        Input:
            data (dict): The JSON dictionary containing the action steps.
        
        Output:
            bool: True if task is marked as done, False otherwise.
        """
        actions = data.get("action", [])
        if not actions:
            return False

        last_action = actions[-1]
        return "done" in last_action



            
                 
             


        
