from typing import Optional, Dict, List, Any
from langchain_core.language_models.chat_models import BaseChatModel
import json

from app.LLM.memory import Memory, Message
from app.Types.agent_types import SystemInfo, LLMConfig, StepStatus, ROLE_TYPE
from app.helper import update_memory
from app.Prompts.interaction import INTERACTION_AGENT_PROMPT
from app.LLM.llm_factory import LLMFactory
from app.Task.task_manager import task_manager
from app.API.websocket_utils import send_ws_message
from app.Adapters.screenshot_parser import get_parsed_screen, get_parsed_screen_xml


class InteractionAgent():

    def __init__(self,  llm: BaseChatModel, task_id: str, chat_id: str, memory: Optional[Memory] = None, maxTokens: int = 128000) -> None:
        self.llm = llm
        self.task_id = task_id
        self.chat_id = chat_id
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
        # self.task_manager = TaskManager()
        

    async def invoke(self, query: str, tool_call_id: str, system_info: Optional[SystemInfo | str] = None ):
        """ Invokes the Interaction Agent which performs interaction on device on the behalf of user.

        Input:
            query (str): The user's input or task description to be processed by the agent.
            tool_call_id (str): An identifier used to track the specific tool call for logging or coordination.
            system_info (Optional[SystemInfo | str]): Information about the current system environment (OS, apps, etc).
        """
        try:
            # Get web socket from task manager
            task_state = task_manager.get_state(self.task_id)
            self.websocket = task_state.websocket
            self.query = query

            # send WS message to client - (inside interaction agent)
            # Notify client present inside Main Agent
            await send_ws_message(
                websocket=self.websocket,
                type="aura_status",
                task_id=self.task_id,
                chat_id=self.chat_id,
                payload={
                    "query": self.query,
                    "message": "Running <INTERACTION AGENT>",
                    "status": "processing",
                }
            )

            # ⏸ Pause check before heavy run
            await task_manager.wait_if_paused(self.task_id)

            self.system_info = system_info
            self.interaction_tool_id = tool_call_id

            # get_current_state_screenshot = {
            #     "type": "screenshot",
            #     "return_format": "base64",
            #     "resize": [1920, 1080],
            #     "quality": 50
            # }

            # Send messsage to client too get the screeshot of users current window in base64 format
            await send_ws_message(
                websocket=self.websocket,
                type="client_tool_request",
                task_id=self.task_id,
                chat_id=self.chat_id,
                payload={
                    "tool": "screenshot",
                    "input": {}
                }
            )
            
            # WIP** - The screenshot is the type client_tool_response it is not the user_input (chage how we are accessing screenshot)
    
            # Waiting for base64 image (screenshot)
            user_input = await task_manager.wait_for_input(self.task_id)
            response_type = user_input.get("type")


            # Re Run if Failed to get screenshot from client
            if response_type == "client_tool_response":
                payload = user_input.get("payload")

                if payload.get("tool") == "screenshot":
                    base64_image = payload.get("result").get("image_base64")
                    resolution = payload.get("result").get("resolution")
            else :
                # NNA-check it once there no such need to send this to client (NNA -> Not Necessary to add) Insted of this retry for screenshot.
                await send_ws_message(
                    websocket=self.websocket,
                    type="error_message",
                    task_id=self.task_id, # New Parameter task_id
                    chat_id=self.chat_id,
                    payload={
                        "error_code": "CLIENT_TOOL_FAILED",
                        "message": "Client Tool failed to get the screenshot"
                    }            
                )
                response = {
                    "status": "failed",
                    "message": "Failed to capture the screenshot"  
                }
                return response
            
            
            height = resolution["height"]
            width = resolution["width"]

            parsed_screen = await get_parsed_screen(base64_image=base64_image, screen_height=height, screen_width=width)
            
            self.subagent_memory.add_messages(self.shared_memory.messages)

            response = await self.think(query, self.subagent_memory.messages, base64_image, parsed_screen)
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

    # WIP** - Make sure the final response going to client or getting add in memory should be readable.
    async def think(self, query: str, chat_history: List[Message], base64_image: str, parsed_screen: str):
        """ Core reasoning loop for the Interaction Agent.

        This method runs a ReAct-style agent loop for a maximum number of steps (`self.max_steps`), 
        invoking the LLM at each step to process the query and generate the response.

        Input:
            query (str): The user's original request or task instruction.
            chat_history (List[Message]): The ongoing chat memory to provide full context to the LLM.
            base64_image (str): A base64-encoded screenshot representing the current UI or system state.
            parsed_screen (str): A XML string representing the elements present on the screen.
        """

        last_result = None  # To store the latest result from the agent

        input_message = self.prepare_interaction_agent_messages_for_openai(query=query, chat_history=chat_history)

        update_memory(role="user", content=query, base64_images=[base64_image], memory=self.subagent_memory)

        for step in range(self.max_steps):
            try:
                # GET_MESSAGE_FROM_CLIENT - current screenshot.

                # ⏸ Pause check before invoking LLM for interaction agent
                await task_manager.wait_if_paused(self.task_id)

                if await self.is_terminate():
                    # Logging can be added here if needed
                    return {
                        "status": "failed",
                        "message": "Terminating due to max failure or explicit termination signal",
                        "result": None
                    }

                result = await self.llm_factory.invoke_interaction_agent(
                    base64_image=base64_image,
                    llm=self.llm,
                    agent_type="interaction",
                    input_message=input_message,
                    parsed_screen_context=parsed_screen
                )

                last_result = result  # Save the latest result in case we need to return it later
                
                print(f"✅RESULT: {last_result}\n\n")

                # Update local memory with the conversation
                update_memory(role="assistant", content=json.dumps(result), memory=self.subagent_memory)
                input_message.append({"role":"assistant", "content":json.dumps(result)})
                
                # Check if task has been completed
                if await self.is_task_completed(result):

                    final_result = result.get("action", [{}])[0].get("done", {})
                    message = final_result.get("message")
                    # WIP** - from here this message is aading to main memory (complete it in the way so that we get entire results of this tool in summarized way)
                    update_memory(
                        role="tool",
                        content=json.dumps(message),
                        name="interaction",
                        tool_call_id=self.interaction_tool_id,
                        base64_images=[base64_image],
                        memory=self.shared_memory
                    )
                    response = {
                        "status": "success",
                        "message": "Task completed successfully",
                        "result": message,
                        "step": step + 1
                    }
                    
                    # What to give the type for this
                    await send_ws_message(
                        websocket=self.websocket,
                        type="server_tool_response",
                        task_id=self.task_id, # New Parameter task_id
                        chat_id=self.chat_id,
                        payload={
                            "tool": "interaction",
                            "content": {
                                "role": "tool",
                                "message": message,
                                "status": "success"
                            }
                        }
                    )

                    return response
                
                # WIP** - Just provide the actions array to the client tool
                # SEND_RESPONSE_TO_CLINET - Interaction agent output
                # 🐤 Send actions to the client
                await send_ws_message(
                    websocket=self.websocket,
                    type= "client_tool_request",
                    task_id=self.task_id, # New Parameter task_id
                    chat_id=self.chat_id,
                    payload={
                        "tool": "interaction",
                        "input": last_result
                    }
                )
                
                # Waiting for base64 image (screenshot)
                tool_resp = await task_manager.wait_for_input(self.task_id)

                response_type = tool_resp.get("type")


                # Re Run if Failed to get screenshot from client
                if response_type == "client_tool_response":
                    payload = tool_resp.get("payload")

                    if payload.get("tool") == "interaction":
                        base64_image = payload.get("result").get("image_base64")
                        resolution = payload.get("result").get("resolution")
                else :
                    # NNA - check it once.  Insted of this write retry logic.
                    # await send_ws_message(
                    #     websocket=self.websocket,
                    #     type="response",
                    #     status="ERROR",
                    #     query=self.query,
                    #     message="Failed to capture screenshot",
                    #     task_id=self.task_id # New Parameter task_id
                    # )
                    response = {
                        "status": "failed",
                        "message": "Interaction agent failed to capture the screenshot"
                    }
                    return response
                
                height = resolution["height"]
                width = resolution["width"]

                parsed_screen = await get_parsed_screen(base64_image=base64_image, screen_height=height, screen_width=width)
                
            except Exception as e:
                # Optional: Add logging here
                response = {
                    "status": "failed",
                    "step": step + 1,
                    "message": f"Exception occurred: {str(e)}",
                    "result": None
                }
                # WIP**: Why we are triggering screenshot tool here? 
                await send_ws_message(
                    websocket=self.websocket,
                    type = "client_tool_request",
                    task_id=self.task_id,
                    chat_id=self.chat_id,
                    payload={
                        "tool": "screenshot",
                        # "input": get_current_state_screenshot
                    }
                )

                return response

        # If max steps are exhausted but task not marked complete
        response = {
            "status": "failed",
            "message": "Maximum steps reached without completing the task - Task Incompleted"
        }


        #  NNA - check it once before addin to client
        # What to give the type for this
        # await send_ws_message(
        #     websocket=self.websocket,
        #     type="response",
        #     status="processing",
        #     query=self.query,
        #     data=response["reason"],
        #     message="Interation agent task completed",
        #     task_id=self.task_id # New Parameter task_id
        # )

        return response
                

    
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

    def prepare_interaction_agent_messages_for_openai(
        self,
        chat_history: List[Message],
        query: str
    ) -> List[dict]:
        """
        Prepares a message list in OpenAI format for a multimodal interaction agent

        Inputs:
            system_prompt (str): System instructions for the LLM.
            chat_history (List[dict]): Prior conversation in OpenAI format.
            query (str): User’s new input or command.

        Output:
            List[dict]: Messages to send to the LLM.
        """
        input_messages = []
        system_prompt = self.interaction_agent_prompt
        system_info = self.system_info

        # 1. Add system prompt
        if system_prompt:
            input_messages.append({
                "role": "system",
                "content": system_prompt
            })
        
        # 2. Add past message objects
        input_messages.extend([message.to_dict() for message in chat_history])

        # 3. Add current user query
        input_messages.append({
            "role": "user",
            "content": [{"type": "text", "text":f"query:{query}\nsystem_info:{system_info}"}]
        })

        return input_messages

            
                 
             


        
