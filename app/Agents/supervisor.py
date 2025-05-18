from typing import Optional, Dict, List, Any, TYPE_CHECKING
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.language_models.chat_models import BaseChatModel

from app.Agents.base_agent import BaseAgent
from app.Types.agent_types import SystemInfo, LLMConfig, StepStatus, ROLE_TYPE
from app.Agents.planner import PlannerAgent
from app.Prompts.supervisor import SUPERVISOR_PROMPT
from app.LLM.llm_factory import LLMFactory
from app.LLM.memory import Message, Memory
from app.helper import update_memory

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

    def __init__(self, llm: BaseChatModel, memory: Optional[Memory] = None, tools: Optional["Tools"] = None, maxTokens: int = 128000):
        # self.query = query; #WIP (need to see if query is required while init)
        self.llm = llm
        self.max_tokens = maxTokens
        self.planner_agent = PlannerAgent()
        self.router_parser = JsonOutputParser()
        self.system_info = None
        self.memory = memory
        self.supervisor_tool_id = None
        self.llm_factory = LLMFactory(self.memory)
        self.supervisor_prompt = SUPERVISOR_PROMPT
        self.tools = tools


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
        
    async def invoke(self, query: str, tool_call_id: str, system_info: Optional[SystemInfo | str] = None, screenshot: Optional[str] = None,) -> str:
        """
        Entry point to handle a user query. It stores the query in memory, calls
        the planner agent to generate a plan, and then executes the plan using either
        a simple or complex task execution flow.

        Input:
            query (str): The user query.
            system_info (Optional[SystemInfo]): Optional contextual system data.
            screenshot (Optional[str]): Optional base64 screenshot for additional context.

        Returns:
            str: The final response or result after processing the plan.
        """
        try:
            self.system_info = system_info
            self.supervisor_tool_id = tool_call_id

            update_memory(role="user", content=query, memory=self.memory, base64_image=screenshot)

            plan = await self.processQuery(query)

            # Execute plan based on complexity
            if len(plan) == 1:
                # logger.info("Processing as simple task")  WIP
                result = await self.handle_simple_task(plan, base64_image=screenshot)
            else:
                # logger.info("Processing as complex task")
                result = await self.handle_complex_task(plan, base64_image=screenshot)

            # Store final result in memory
            update_memory(role="assistant", content=result, memory=self.memory)
            return result

        except Exception as e:
            # logger.error(f"Error in SupervisorAgent.invoke: {str(e)}") WIP
            error_message = f"An error occurred while processing your request: {str(e)}"
            update_memory(role="assistant", content=error_message, memory=self.memory)
            return error_message

    async def handle_simple_task(self, plan: List[Dict[str, Any]], base64_image: Optional[str] = None) -> str:
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
            result = await self.run_step(step=step, base64_image=base64_image)
            return result
        except Exception as e:
            # logger.error(f"Error executing simple task: {str(e)}")
            update_memory(role="assistant", content=f"I encountered an error while processing your request: {str(e)}", memory=self.memory)
            return f"Failed to complete the task: {str(e)}"
    
    async def handle_complex_task(self, plan: List[Dict[str, Any]], base64_image: Optional[str] = None) -> str:
        """
        Executes a multi-step plan where steps may have dependencies. Tracks
        step completion status and retries failed steps up to a limit.

        Input:
            plan (List[Dict[str, Any]]): The structured plan containing multiple steps.

        Returns:
            str: The result of the final step or an error message if steps failed.
        """
        if not plan:
            raise ValueError("Complex task plan cannot be empty")
        
        # logger.info(f"Processing complex task with {len(plan)} steps")
        
        # Track status of each step
        step_status = {step["id"]: StepStatus.PENDING for step in plan}
        max_attempts = 3
        attempts = 0
        
        # Continue until all steps are completed or max attempts reached
        while StepStatus.PENDING in step_status.values() and attempts < max_attempts:
            attempts += 1
            # logger.info(f"Complex task execution attempt {attempts}")
            
            for step in plan:
                step_id = step["id"]
                
                # Skip already completed steps
                if step_status[step_id] == StepStatus.COMPLETED:
                    continue
                
                # Check if dependencies are met
                dependencies = step.get("dependency", [])
                deps_met = all(
                    step_status.get(dep, StepStatus.FAILED) == StepStatus.COMPLETED 
                    for dep in dependencies
                )
                
                if not deps_met:
                    # logger.info(f"Skipping step {step_id} - dependencies not met")
                    continue
                
                try:
                    # logger.info(f"Executing step {step_id}: {step.get('description', 'No description')}")
                    result = await self.run_step(step=step, base64_image=base64_image)
                    self.step_results[step_id] = result
                    step_status[step_id] = StepStatus.COMPLETED
                    # logger.info(f"Step {step_id} completed successfully")
                except Exception as e:
                    # logger.error(f"Error executing step {step_id}: {str(e)}")
                    step_status[step_id] = StepStatus.FAILED
                    update_memory(role="assistant", content=f"Failed to complete step {step_id}: {str(e)}", memory=self.memory)
        
        # Check if all steps completed successfully
        if all(status == StepStatus.COMPLETED for status in step_status.values()):
            final_step = plan[-1]
            final_result = self.step_results.get(final_step["id"], "Task completed but no final result available")
            return final_result
        else:
            failed_steps = [s_id for s_id, status in step_status.items() if status == StepStatus.FAILED]
            pending_steps = [s_id for s_id, status in step_status.items() if status == StepStatus.PENDING]
            error_msg = f"Failed to complete all steps. Failed steps: {failed_steps}. Pending steps: {pending_steps}"
            # logger.error(error_msg)
            return error_msg

    async def run_step(self, step: Dict[str, Any], base64_image: Optional[str] = None) -> str: # WIP
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

            if description:
                response = await self.llm_factory.agent_executor(
                    system_prompt=self.supervisor_prompt,
                    llm=self.llm,
                    query=description,
                    agent_type="supervisor",
                    system_info=self.system_info,
                    tools=tools,
                    chat_history=chat_history,
                    screenshot=base64_image
                )
                return response
            
            else:
                raise ValueError("Task description or subagent name not found while running step")
            
        except Exception as e:
            # logger.error(f"Error in run_step: {str(e)}")
            raise RuntimeError(f"Error while runing the Step given by the Planner-Agnet, Error: {e}")