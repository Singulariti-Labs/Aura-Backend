import json
import time
from typing import List
from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_classic.schema.agent import AgentAction, AgentFinish
from app.LLM.memory import Memory
from app.helper import update_memory

# Pricing per 1M tokens (input, output)
PRICING = {
    'openai:gpt-4o':               (2.50, 10.00),
    'openai:gpt-4o-mini':          (0.15,  0.60),
    'anthropic:claude-3-5-sonnet': (3.00, 15.00),
    'anthropic:claude-sonnet-4':   (3.00, 15.00),
    'google:gemini-1.5-pro':       (3.50, 10.50),
}


class AgentCallbackHandler(BaseCallbackHandler):
    def __init__(self, memory: Memory):
        self.memory = memory
        self.current_usage = None
        self.current_details = {}
        self.llm_start_time = None

    def _print_action(self, action: AgentAction):
        input_str = json.dumps(action.tool_input, indent=2) if isinstance(action.tool_input, dict) else str(action.tool_input)
        lines = input_str.split('\n')
        print("\n╔" + "═"*58 + "╗")
        print(f"║ {'🤖 ASSISTANT ACTION':^56} ║")
        print("╟" + "─"*58 + "╢")
        print(f"║ 🛠️  TOOL  : {action.tool:<46} ║")
        for i, line in enumerate(lines):
            prefix = "📥 INPUT : " if i == 0 else "           "
            print(f"║ {prefix}{line:<46} ║")
        print("╚" + "═"*58 + "╝\n")

    def _compute_cost(self, input_tokens: int, output_tokens: int, provider: str, model_name: str) -> float:
        rates = PRICING.get(f"{provider}:{model_name}", (5.0, 15.0))
        return round((input_tokens * rates[0] + output_tokens * rates[1]) / 1_000_000, 6)

    def on_llm_start(self, serialized, prompts, **kwargs):
        self.llm_start_time = time.time()

    def on_llm_end(self, response, **kwargs):
        llm_end_time = time.time()
        llm_start_time = self.llm_start_time or llm_end_time
        llm_duration_ms = round((llm_end_time - llm_start_time) * 1000, 2)

        llm_out = getattr(response, 'llm_output', {}) or {}
        usage = llm_out.get('token_usage') or llm_out.get('usage') or {}

        if not usage:
            generations = getattr(response, 'generations', None) or []
            if generations:
                msg = getattr(generations[0][0], 'message', None)
                usage = getattr(msg, 'usage_metadata', {}) or {}

        provider   = llm_out.get('provider')   or getattr(response, 'lc_provider', 'unknown')
        model_name = llm_out.get('model_name') or getattr(response, 'lc_model_name', 'unknown')

        response_metadata = getattr(response, 'response_metadata', None) or {}
        finish_reason = llm_out.get('finish_reason') or response_metadata.get('finish_reason', 'unknown')

        input_t  = usage.get('input_tokens',  usage.get('prompt_tokens', 0))
        output_t = usage.get('output_tokens', usage.get('completion_tokens', 0))
        cost     = self._compute_cost(input_t, output_t, provider, model_name)

        self.current_usage = {
            'input': input_t,
            'output': output_t,
            'total_tokens': input_t + output_t,
            'cost': cost,
            # 'input_token_details':  llm_out.get('input_token_details',  {'audio': 0, 'cache_read': 0}),
            # 'output_token_details': llm_out.get('output_token_details', {'audio': 0, 'reasoning': 0}),
        }
        self.current_details = {
            'provider': provider,
            'model_name': model_name,
            'finish_reason': finish_reason,
            'llm_start_time':  round(llm_start_time, 3),
            'llm_end_time':    round(llm_end_time, 3),
            'llm_duration_ms': llm_duration_ms,
        }

        print(f"\n📊 {provider}:{model_name} | I:{input_t} O:{output_t} | ${cost:.6f} | {finish_reason} | ⏱️ {llm_duration_ms}ms")

    def _save(self, content: str, tool_calls=None):
        update_memory(role="assistant", content=content, memory=self.memory,
                      tool_calls=tool_calls, usage=self.current_usage, details=self.current_details)

    def _reset(self):
        self.current_usage = None
        self.current_details = {}
        self.llm_start_time = None

    def on_agent_action(self, action: AgentAction, **kwargs):
        self._print_action(action)
        self._save(action.log.strip(), [{"type": "tool_call",
                                         "id": getattr(action, 'tool_call_id', f"call_{id(action)}"),
                                         "name": action.tool,
                                         "input": action.tool_input}])
        self._reset()

    def on_agent_multi_action(self, actions: List[AgentAction], **kwargs):
        tool_calls, reasoning = [], ""
        for action in actions:
            self._print_action(action)
            tool_calls.append({"type": "tool_call",
                                "id": getattr(action, 'tool_call_id', f"call_{id(action)}"),
                                "name": action.tool,
                                "input": action.tool_input})
            reasoning = reasoning or action.log.strip()
        self._save(reasoning, tool_calls)
        self._reset()

    def on_agent_finish(self, finish: AgentFinish, **kwargs):
        self._save(finish.return_values.get("output", ""))
        self._reset()