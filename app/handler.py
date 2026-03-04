import json
from typing import List, Any
from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_classic.schema.agent import AgentAction
from app.LLM.memory import Memory
from app.helper import update_memory

class AgentCallbackHandler(BaseCallbackHandler):
    def __init__(self, memory: Memory):
        self.memory = memory
    
    def _print_action(self, action: AgentAction):
        """Pretty Print the Tool Call for Debugging/Visibility"""
        print("\n" + "╔" + "═"*58 + "╗")
        print(f"║ {'🤖 ASSISTANT ACTION':^56} ║")
        print("╟" + "─"*58 + "╢")
        print(f"║ 🛠️  TOOL  : {action.tool:<46} ║")
        
        # Format input for display
        try:
            if isinstance(action.tool_input, dict):
                input_str = json.dumps(action.tool_input, indent=2)
                # Handle multi-line indentation for the box
                lines = input_str.split('\n')
                print(f"║ 📥 INPUT : {lines[0]:<46} ║")
                for line in lines[1:]:
                    print(f"║           {line:<46} ║")
            else:
                print(f"║ 📥 INPUT : {str(action.tool_input):<46} ║")
        except:
             print(f"║ 📥 INPUT : {str(action.tool_input):<46} ║")
             
        print("╚" + "═"*58 + "╝\n")

    def on_agent_action(self, action: AgentAction, **kwargs):
        """On invoking a single action, stores it in Memory."""
        self._print_action(action)
        
        # Capture the assistant's reasoning
        reasoning_message = action.log.strip()
        
        # Extract tool_call_id if available, otherwise generate a placeholder
        tool_call_id = getattr(action, 'tool_call_id', f"call_{id(action)}")

        # Construct a list containing this single tool call
        tool_calls = [{
            "type": "tool_call",
            "id": tool_call_id,
            "name": action.tool,
            "input": action.tool_input
        }]

        # Store in memory using the updated structure
        update_memory(
            role="assistant", 
            content=reasoning_message, 
            memory=self.memory,
            tool_calls=tool_calls
        )

    def on_agent_multi_action(self, actions: List[AgentAction], **kwargs):
        """Handles multiple tool calls in a single assistant message block."""
        tool_calls = []
        reasoning_message = ""
        
        for action in actions:
            # Print each action for visibility
            self._print_action(action)
            
            # Extract tool_call_id
            tool_call_id = getattr(action, 'tool_call_id', f"call_{id(action)}")
            
            # Append to our list of calls
            tool_calls.append({
                "type": "tool_call",
                "id": tool_call_id,
                "name": action.tool,
                "input": action.tool_input
            })
            
            # Capture the reasoning (usually identical for all actions in a multi-action turn)
            if not reasoning_message and action.log:
                reasoning_message = action.log.strip()

        # Bundle ALL tool calls into a single Assistant message in memory
        update_memory(
            role="assistant", 
            content=reasoning_message, 
            memory=self.memory,
            tool_calls=tool_calls
        )
