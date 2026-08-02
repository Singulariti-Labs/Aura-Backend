from typing import Optional, Dict, List, Any, TYPE_CHECKING
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.language_models.chat_models import BaseChatModel
from asyncpg import Pool

from app.Agents.base_agent import BaseAgent
from app.Types.agent_types import SystemInfo, LLMConfig, StepStatus, ROLE_TYPE, AuraConfig
from app.Agents.planner import PlannerAgent
from app.Prompts.supervisor import SUPERVISOR_PROMPT
from app.LLM.llm_factory import LLMFactory
from app.LLM.memory import Message, Memory
from app.helper import update_memory, send_last_assistant_message
from app.Task.task_manager import task_manager
from app.api.websocket_utils import send_ws_message
from app.Prompts.aura_new import buildAuraSystemPrompt

if TYPE_CHECKING:
    from app.Tools.tool_calling import Tools

class SupervisorAgent(BaseAgent):
    """
    SupervisorAgent is the central coordinator responsible for handling incoming user queries.
    It works in collaboration with the PlannerAgent to break down tasks into actionable steps.
    Based on the complexity of the plan (simple or multi-step), it invokes appropriate sub-agents
    or tools to execute each step. The class also maintains conversation memory and handles
    task dependencies, retries, and result aggregation.
    """

    def __init__(self, llm: BaseChatModel, task_id: str, chat_id: str, memory: Optional[Memory] = None, tools: Optional["Tools"] = None, maxTokens: int = 128000, aura_config: Optional[AuraConfig] = None, history: List[Dict] = [], llm_provider: Optional[str] = None, dbpool: Optional[Pool] = None, user_id: Optional[str] = None, rate_limit_loop: Optional[Any] = None):
        # self.query = query; #WIP (need to see if query is required while init)
        self.llm = llm
        self.max_tokens = maxTokens
        self.task_id = task_id
        self.chat_id = chat_id
        self.planner_agent = PlannerAgent()
        self.router_parser = JsonOutputParser()
        self.system_info = None
        self.memory = memory
        self.supervisor_tool_id = None
        self.dbpool = dbpool
        self.user_id = user_id
        self.rate_limit_loop = rate_limit_loop
        self.llm_provider = llm_provider
        self.llm_factory = LLMFactory(
            self.memory,
            rate_limit_pool=self.dbpool,
            user_id=self.user_id,
            rate_limit_loop=self.rate_limit_loop,
            fallback_provider=self.llm_provider,
            fallback_model_name=(
                getattr(self.llm, "model_name", None)
                or getattr(self.llm, "model", None)
            ),
        )
        self.supervisor_prompt = SUPERVISOR_PROMPT
        self.tools = tools
        self.step_results = {}
        self.validate_response = False
        self.aura_config = aura_config or AuraConfig()
        self.history = history
        # self.task_manager = TaskManager()


    async def processQuery(self, query: str) -> str:
        """
        Passes the user query to the PlannerAgent to generate a structured plan
        containing one or more steps to accomplish the task.

        Input:
            query (str): The user's input query.

        Returns:
            str: A serialized plan representing the task breakdown.
        """
        result = await self.planner_agent.run(self.llm, query)
        return result
        
    async def invoke(self, query: str, tool_call_id: str, system_info: Optional[SystemInfo | str] = None) -> str:
        """
        Entry point to handle a user query. It stores the query in memory, calls
        the planner agent to generate a plan, and then executes the plan using either
        a simple or complex task execution flow.

        Input:
            query (str): The user query.
            system_info (Optional[SystemInfo]): Optional contextual system data.

        Returns:
            str: The final response or result after processing the plan.
        """
        try:
            # Get web socket from task manager
            task_state = task_manager.get_state(self.task_id)
            self.websocket = task_state.websocket
            self.query = query

            # Notify client present inside Main Agent
            await send_ws_message(
                websocket=self.websocket,
                type="aura_status",
                task_id=self.task_id,
                chat_id=self.chat_id,
                payload={
                    "query": self.query,
                    "message": "Running <SUPERVISOR AGENT>",
                    "status": "processing",
                }
            )

            # ⏸ Pause check before any heavy work
            await task_manager.wait_if_paused(self.task_id)

            self.system_info = system_info
            self.supervisor_tool_id = tool_call_id

            # update_memory(role="user", content=query, memory=self.memory, base64_image=screenshot)

            # ⏸ Pause check before any planning
            await task_manager.wait_if_paused(self.task_id)

            plan = await self.processQuery(query)

            # ⏸ Pause check before running planed steps
            await task_manager.wait_if_paused(self.task_id)

            # Execute plan based on complexity
            if len(plan) == 1:
                # logger.info("Processing as simple task")  WIP
                result = await self.handle_simple_task(plan)
            else:
                # logger.info("Processing as complex task")
                result = await self.handle_complex_task(plan)

            return result

        except Exception as e:
            # logger.error(f"Error in SupervisorAgent.invoke: {str(e)}") WIP
            error_message = f"An error occurred while processing your request: {str(e)}"
            update_memory(role="assistant", content=error_message, memory=self.memory)
            return error_message

    async def handle_simple_task(self, plan: List[Dict[str, Any]]) -> str:
        """
        Executes a single-step plan as a simple task.

        Input:
            plan (List[Dict[str, Any]]): The plan containing a single task step.

        Returns:
            str: The result from executing the single step.

        Raises:
            ValueError: If the plan does not contain exactly one step.
        """

        if not plan or len(plan) != 1:
            raise ValueError("Simple task should have exactly one step")
        
        step = plan[0]
        # logger.info(f"Processing simple task: {step.get('description', 'No description')}")
        # logger: Log the performing step-no/Total steps
        
        try:
            # ⏸ Pause check before run_step
            await task_manager.wait_if_paused(self.task_id)

            result = await self.run_step(step=step)

            if self.validate_response:
                validator_result = await self.response_validator(
                    query=step.get("description", "No description"),
                    response=result["output"],
                    expected_output=step.get("expected-output", "No expected output")
                )

                if validator_result["is_valid"]:
                    return result
                else:
                    return f"Step {step.get('description')} failed because {validator_result['reason']}"
                
            return result
        except Exception as e:
            # logger.error(f"Error executing simple task: {str(e)}")
            update_memory(role="assistant", content=f"I encountered an error while processing your request: {str(e)}", memory=self.memory)
            return f"Failed to complete the task: {str(e)}"
    
    async def handle_complex_task(self, plan: List[Dict[str, Any]]):
        """
        Executes a multi-step plan where each step is tracked with detailed status.
        Stops and returns error details if any step fails after max retries.
        """
        if not plan:
            raise ValueError("Plan is not provided! Please provide a plan to complete a complex task.")

        step_responses = []
        max_attempts = 3

        for index, step in enumerate(plan, start=1):
            step_id = step.get("id", f"Step{index}")
            description = step.get("description", "No description")
            attempts = 0
            step_status = StepStatus.PENDING
            response = None

            while attempts < max_attempts:
                attempts += 1
                try:
                    # ⏸ Pause check before run_step
                    await task_manager.wait_if_paused(self.task_id)

                    response = await self.run_step(step=step)
                    self.step_results[step_id] = response

                    if self.validate_response:
                        validator_result = await self.response_validator(
                            query=description,
                            response=response["output"],
                            expected_output=step.get("expected-output")
                        )

                        if validator_result["is_valid"]:
                            step_status = StepStatus.COMPLETED
                        else:
                            step_status = StepStatus.FAILED
                            print(f"Step: {description} Failed because {validator_result['reason']}")
                    else:
                        step_status = StepStatus.COMPLETED
                    break  # Exit retry loop on success
                except Exception as e:
                    response = f"Attempt {attempts} failed: {str(e)}"
                    step_status = StepStatus.FAILED

            # Record step status after attempts
            step_info = {
                "step_no": index,
                "step_id": step_id,
                "step_description": description,
                "step_status": step_status.value,
                "step_response": response,
            }
            step_responses.append(step_info)

            if step_status == StepStatus.FAILED:
                # Update memory and return detailed error info
                update_memory(
                    role="assistant",
                    content=(
                        f"❌ Step {index} (ID: {step_id}) failed after {max_attempts} attempts.\n"
                        f"Description: {description}\nError: {response}"
                    ),
                    memory=self.memory
                )
                error_summary = (
                    f"❌ Execution stopped at step {index} (ID: {step_id}).\n"
                    f"Description: {description}\n"
                    f"Error: {response}\n"
                    f"Steps Executed:\n{step_responses}"
                )
                return error_summary

        # All steps completed successfully
        return step_responses

    async def run_step(self, step: Dict[str, Any]) -> str: # WIP
        """
        Executes a single step by invoking the relevant sub-agent or tool.
        Prepares context using memory and LLM before executing the tool.

        Input:
            step (Dict[str, Any]): A dictionary describing the step to be executed.

        Returns:
            str: The result from the tool/sub-agent execution.

        Raises:
            Exception: If the tool execution or LLM call fails.
        """
        try:
            # Extract step information
            step_id = step.get("id", None)
            description = step.get("description", None)
            thought = step.get("thought", "")
            expected_output = step.get("expected-output", "")

            tools = self.tools.get_supervisor_tools()
        
            chat_history = self.memory.messages

            # ⏸ Pause check before running step
            await task_manager.wait_if_paused(self.task_id)

            if description:
                response = await self.llm_factory.agent_executor(
                    system_prompt=self.supervisor_prompt,
                    llm=self.llm,
                    query=description,
                    agent_type="supervisor",
                    system_info=self.system_info,
                    tools=tools,
                    chat_history=chat_history
                )
                return response
            
            # SEND_RESPONSE_TO_CLIENT - Supervisor agent output
            else:
                raise ValueError("Task description or subagent name not found while running step")
            
        except Exception as e:
            # logger.error(f"Error in run_step: {str(e)}")
            raise RuntimeError(f"Error while runing the Step given by the Planner-Agnet, Error: {e}")


    async def response_validator(self, query: str, response: str, expected_output: str):
        """
        Validates the response against the expected output.
        """
        try:
            # Parse the response into a structured format
            result = await self.llm_factory.response_validator(llm=self.llm, query=query, response=response, expected_output=expected_output)
            return result
        except Exception as e:
            raise RuntimeError(f"Validation failed while valaditing response: {e}")

    async def invoke_aura(self, query: str, tool_call_id: str, system_info: Optional[SystemInfo | str] = None):
        """
        Method to invoke aura agent, starting point of agentinc flow to complete complex task
        
        inputs:
            query (str): users query,
            system_info (Optional[SystemInfo]): Optional users system related data
            tool_call_id (str): unique id for a tool call.
        """ 

        try:
             # Get web socket from task manager
            task_state = task_manager.get_state(self.task_id)
            self.websocket = task_state.websocket
            self.query = query

            # Get all the tools for the Aura
            tools = self.tools.get_supervisor_tools()

            prompt = buildAuraSystemPrompt(
                system_info=system_info,
                tools=tools,
                chat_id=self.chat_id,
                task_id=self.task_id,
                config=self.aura_config,
            )

            result = None
            # LLM call
            if query:
                result = await self.llm_factory.aura_executor(
                    query=query,
                    system_prompt=prompt,
                    tools=tools,
                    system_info=system_info,
                    llm=self.llm,
                    llm_provider=self.llm_provider or self.llm_factory.detect_provider_from_llm(self.llm),
                    agent_type="aura",
                    history=self.history
                )

                final_result = None
                if "output" in result:
                    final_result = result.get("output")
                else:
                    final_result = "Aura LLM run failed, task failed to complete successfull."

                # SEND_RESPONSE_TO_CLIENT - Supervisor agent output
                await send_ws_message(
                    websocket=self.websocket,
                    task_id=self.task_id,
                    chat_id=self.chat_id,
                    type="aura_message",
                    payload={
                        "content": {
                            "role": "assistant",
                            "message": final_result,
                        },
                        "coming_from": "supervisor/server"
                    }
                )

                # await send_last_assistant_message(
                #     task_id=self.task_id,
                #     chat_id=self.chat_id,
                #     memory=self.memory,
                #     message_type="aura_message",
                #     coming_from="supervisor/server"
                # )

                return final_result

            else:
                return ("Aura run failed, input query not available")
            
        except Exception as e:
            error_message = f"An error occurred while processing your request, Aura run failed: {str(e)}"
            update_memory(role="tool", name = "aura", tool_call_id=tool_call_id, content=error_message, memory=self.memory)
            return error_message
