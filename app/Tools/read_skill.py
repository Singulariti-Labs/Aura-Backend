import os
import uuid
from typing import Optional
from langchain_core.language_models.chat_models import BaseChatModel

from app.Tools.base_tool import BaseTool
from app.LLM.memory import Memory
from app.Types.agent_types import ReadSkillToolInput
from app.helper import send_last_assistant_message
from app.Agentic_Tools.read_skill_tool import SkillLoader

class ReadSkillTool(BaseTool):
    def __init__(self, llm: BaseChatModel, task_id: str, chat_id: str, memory: Optional[Memory] = None):
        """
        Initializes the Read Skill Tool.
        """
        super().__init__(
            name="read_skill",
            description="""Read a specified skill to make its specialized capabilities and domain knowledge available for the current task.
            Provide the skill_name (folder name) and its path (location). Use 'default_skill' for path if it's a default skill.""",
            memory=memory,
            args_schema=ReadSkillToolInput
        )
        self.chat_id = chat_id
        self.task_id = task_id
        self.skill_loader = SkillLoader(llm=llm, task_id=task_id, chat_id=chat_id, memory=memory)

    async def run(self, inputs: ReadSkillToolInput):
        """
        Executes the logic to read a skill.
        """
        # Sending the last assistant message to the client.
        await send_last_assistant_message(memory=self.memory, task_id=self.task_id, chat_id=self.chat_id, tool_name="read_skill")
        
        skill_name = inputs.skill_name
        path = inputs.path
        arguments = inputs.arguments
        tool_call_id = str(uuid.uuid4())
        
        response = await self.skill_loader.read_skill(
            skill_name=skill_name,
            path=path,
            arguments=arguments,
            tool_call_id=tool_call_id
        )
        return response
