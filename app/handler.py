from langchain.callbacks.base import BaseCallbackHandler
from langchain.schema.agent import AgentAction, AgentFinish
from app.LLM.memory import Message, Memory
from app.helper import update_memory


class AgentCallbackHandler(BaseCallbackHandler):
    def __init__(self, memory: Memory):
        self.memory = memory
    
    def on_agent_action(self, action: AgentAction, **kwargs):
        """ On invoking the agent stores the agent reasoning message in the Memory for agent action trace"""
        # Capture the assistant's reasoning before the tool is called
        reasoning_message = action.log.strip()
        # WIP** - send the reasoning message from wsMessage to client.
        update_memory(role="assistant", content=reasoning_message, memory=self.memory)
