from typing import Optional
from langchain_core.language_models.chat_models import BaseChatModel
import uuid

from app.Tools.base_tool import BaseTool
from app.LLM.memory import Memory
from app.Types.agent_types import AskUserToolInput
from app.helper import send_last_assistant_message
from app.Agentic_Tools.ask_user_tool import AskUser

class AskUserTool(BaseTool):
    def __init__(self, llm: BaseChatModel, task_id: str, chat_id: str, memory: Optional[Memory] = None):
        """
        Initializes the Ask User Tool with a language model and optional memory.
        """
        super().__init__(
            name="ask_user",
            description="""Use this tool when you need to ask the user questions or gather their input during execution. 
            Use it to:
            - Gather preferences or requirements
            - Clarify ambiguous instructions  
            - Get decisions on implementation choices
            - Offer choices about direction
            - Understand specific requirements.
            If you recommend a specific option, make it the first 
            option and add (Recommended) at the end of the label.
            The input should follow the specified structured format.""",
            memory=memory,
            args_schema=AskUserToolInput
        )
        self.chat_id = chat_id
        self.task_id = task_id
        self.ask_user_agentic = AskUser(llm=llm, task_id=task_id, chat_id=chat_id, memory=memory)

    async def run(self, inputs: AskUserToolInput):

        # Sending the last assistant message to the client.
        await send_last_assistant_message(memory=self.memory, task_id=self.task_id, chat_id=self.chat_id, tool_name="ask_user")
        
        tool_call_id = str(uuid.uuid4())
        response = await self.ask_user_agentic.ask_user(
            question=inputs.question,
            type=inputs.type,
            options=inputs.options,
            placeholder=inputs.placeholder,
            required=inputs.required,
            tool_call_id=tool_call_id
        )
        return response
