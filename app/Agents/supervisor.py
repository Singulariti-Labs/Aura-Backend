from typing import Optional, Dict, List, Any
from langchain_core.output_parsers import JsonOutputParser


from app.Agents.base_agent import BaseAgent
from app.Types.agent_types import SystemInfo, LLMConfig, StepStatus
from app.Agents.planner import PlannerAgent
from app.Prompts.supervisor import SUPERVISOR_PROMPT
from app.LLM.llm_factory import LLMFactory
from app.LLM.memory import Message, Memory

class SupervisorAgent(BaseAgent): 
    """ Supervisor is the main AI agent, who is responsible to decides which subagent
        to call to perform specific task given by the PlannerAgent 
    """

    def __init__(self, query: str, llm: LLMConfig, maxTokens: int = 128000):
        self.query = query;
        self.llm = LLMFactory.create_llm(llm);
        self.max_tokens = maxTokens;
        self.planner_agent = PlannerAgent();
        self.router_parser = JsonOutputParser();
        self.system_info = None;


    async def processQuery(self, query: str) -> str:
        """
            Process the query by passing query to Planner Agent get the detailed plane to complete the task.
        """
        result = await self.planner_agent.run(self.llm, self.query)
        return result
        
    async def invoke(self, query: str, systemInfo: SystemInfo, screenShot: Optional[str] = None) -> str:
        """
            Invoke Supervisor Agent.
        """
        try:
            self.system_info = systemInfo

            self.update_memory("user", query, base64_image=screenShot)

            plan = await self.processQuery(query)

            # Execute plan based on complexity
            if len(plan) == 1:
                # logger.info("Processing as simple task")  WIP
                result = await self.handle_simple_task(plan)
            else:
                # logger.info("Processing as complex task")
                result = await self.handle_complex_task(plan)

            # Store final result in memory
            self.update_memory("assistant", result)

        except Exception as e:
            # logger.error(f"Error in SupervisorAgent.invoke: {str(e)}") WIP
            error_message = f"An error occurred while processing your request: {str(e)}"
            self.update_memory("assistant", error_message)
            return error_message    

    # MEMORY - WIP
    def update_memory(
        self,
        role: ROLE_TYPE,  # type: ignore
        content: str,
        base64_image: Optional[str] = None,
        **kwargs,
    ) -> None:
        """Add a message to the agent's memory.

        Args:
            role: The role of the message sender (user, system, assistant, tool).
            content: The message content.
            base64_image: Optional base64 encoded image.
            **kwargs: Additional arguments (e.g., tool_call_id for tool messages).

        Raises:
            ValueError: If the role is unsupported.
        """
        message_map = {
            "user": Message.user_message,
            "system": Message.system_message,
            "assistant": Message.assistant_message,
            "tool": lambda content, **kw: Message.tool_message(content, **kw),
        }

        if role not in message_map:
            raise ValueError(f"Unsupported message role: {role}")

        # Create message with appropriate parameters based on role
        kwargs = {"base64_image": base64_image, **(kwargs if role == "tool" else {})}
        Memory.add_message(message_map[role](content, **kwargs))

    async def handle_simple_task(self, plan: List[Dict[str, Any]]) -> str:
        """
        Handle a simple task with a single step.
        
        Args:
            plan: A list containing a single step
            
        Returns:
            The result of executing the step
        """
        if not plan or len(plan) != 1:
            raise ValueError("Simple task should have exactly one step")
        
        step = plan[0]
        # logger.info(f"Processing simple task: {step.get('description', 'No description')}")
        
        try:
            result = await self.run_step(step)
            return result
        except Exception as e:
            # logger.error(f"Error executing simple task: {str(e)}")
            self.update_memory("assistant", f"I encountered an error while processing your request: {str(e)}")
            return f"Failed to complete the task: {str(e)}"
    
    async def handle_complex_task(self, plan: List[Dict[str, Any]]) -> str:
        """
        Handle a complex task with multiple steps that may have dependencies.
        
        Args:
            plan: A list of steps to execute
            
        Returns:
            The final result after executing all steps
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
                    result = await self.run_step(step)
                    self.step_results[step_id] = result
                    step_status[step_id] = StepStatus.COMPLETED
                    # logger.info(f"Step {step_id} completed successfully")
                except Exception as e:
                    # logger.error(f"Error executing step {step_id}: {str(e)}")
                    step_status[step_id] = StepStatus.FAILED
                    self.update_memory("assistant", f"Failed to complete step {step_id}: {str(e)}")
        
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

    async def run_step(self, step: Dict[str, Any]) -> str: # WIP
        """
        Execute a single step using the appropriate subagent.
        
        Args:
            step: The step to execute
            
        Returns:
            The result of the step execution
        """
        try:
            # Extract step information
            step_id = step.get("id", "unknown")
            description = step.get("description", "No description provided")
            subagent_name = step.get("sub-agent")
            thought = step.get("thought", "")
            expected_output = step.get("expected-output", "")
            
            
            # Create supervisor prompt for LLM
            supervisor_prompt = SUPERVISOR_PROMPT
            
            # Add the supervisor prompt to memory
            self.update_memory("system", supervisor_prompt)
            
            # Invoke LLM to process the step
            response = await self.llm.generate(
                messages=Memory.get_messages(),
                max_tokens=self.max_tokens
            )
            
            # Extract tool/subagent call from response
            tool_name, tool_input = self._parse_tool_call(response, subagent_name)
            
            # Execute the tool/subagent
            tool_result = await self._execute_tool(tool_name, tool_input)
            
            # Update memory with tool response
            self.update_memory("tool", tool_result, tool_call_id=step_id)
            
            # Return the result
            return tool_result
            
        except Exception as e:
            # logger.error(f"Error in run_step: {str(e)}")
            raise