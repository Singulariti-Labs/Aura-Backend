from typing import Optional
from langchain_core.language_models.chat_models import BaseChatModel

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
            description="""Use this tool whenever you need information from the user or gather their inputs during execution. 
            WHEN TO USE:
            - Gather preferences or requirements
            - Clarify ambiguous instructions  
            - Get decisions on implementation choices
            - Offer choices about direction
            - Understand specific requirements.
            - If Explictily user ask you to ask questions or to be consulted, e.g. "ask me questions", "ask me what you need", "let's ideate together", "what do you need from me?, etc
            - If boot_me = true then ask user details using this tool & Mostly questions will be of type input for boot_me = True.
            If you recommend a specific option, make it the first option and add (Recommended) at the end of the label.
            Not need to be recommended always if you are not sure about the best option let it be.
            The input should follow the specified structured format.
            
            WHEN NOT TO USE:
            - Do NOT ask for confirmation like "Should I proceed?" or "Does this look good?" — just proceed.
            - Do NOT call this tool if you already have enough information
            - Do NOT call this tool multiple times in a row — batch ALL questions into a single call.
            Exception: during boot_me = true, multiple sequential calls are allowed to collect user details.
            
            [NOTE]: During boot_me = true, you can use this tool multiple times in a row or in a session, to collect user's details, batch questions so will require less tool calls.

            RULES:
            - questions array: minimum 1, maximum 5
            - id must be unique and snake_case — it becomes the key in the answer dict
            - options to give choices to user, leave empty [] if only text input is allowed. Maximum 4 options.
            - multi_select (true = checkboxes + text box, false = radio + text box), keep it false for only text input.
            - placeholder always available for text input it will be always there, use it to guide user.
            - If you have a preferred option, append "(Recommended)" to that option label
            - If a question is not strictly needed, set required to false
            """,
            memory=memory,
            args_schema=AskUserToolInput
        )
        self.chat_id = chat_id
        self.task_id = task_id
        self.ask_user_agentic = AskUser(llm=llm, task_id=task_id, chat_id=chat_id, memory=memory)

    async def run(self, inputs: AskUserToolInput):

        # Sending the last assistant message to the client and to get tool_call_id
        tool_call_id = await send_last_assistant_message(memory=self.memory, task_id=self.task_id, chat_id=self.chat_id, tool_name="ask_user")
        
        response = await self.ask_user_agentic.ask_user(
            questions=inputs.questions,
            tool_call_id=tool_call_id
        )
        return response
