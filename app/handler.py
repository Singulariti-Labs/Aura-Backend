import json
import time
import pprint
import traceback
from typing import List, Dict, Any, Optional, Union

from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_core.agents import AgentAction, AgentFinish
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult, ChatGeneration

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


def _safe_dict(value: Any) -> dict:
    """
    Returns value if it is a dict, otherwise returns empty dict.
    Prevents 'str object has no attribute get' errors when providers
    return unexpected types for metadata fields.
    """
    return value if isinstance(value, dict) else {}


class AgentCallbackHandler(BaseCallbackHandler):
    """
    Callback handler for LangChain agents.

    REGISTRATION — pass via RunnableConfig to guarantee the handler
    reaches the LLM level (on_chat_model_start / on_chat_model_end):

        handler  = AgentCallbackHandler(memory=memory)
        agent    = create_openai_tools_agent(llm, tools, prompt)
        executor = AgentExecutor(agent=agent, tools=tools, verbose=False)

        response = await executor.ainvoke(
            {"input": ..., "chat_history": ...},
            config=RunnableConfig(callbacks=handler.as_list()),
        )
    """

    def __init__(self, memory: Memory, debug: bool = False):
        super().__init__()
        self.memory = memory
        self.debug  = debug

        # LLM state — populated in _handle_llm_response
        self.latest_llm_usage:    Optional[Dict[str, Any]] = None
        self.latest_llm_details:  Optional[Dict[str, Any]] = None
        self.latest_llm_content:  Optional[str]            = None
        self.latest_llm_response: Optional[Any]            = None

        # Timing + model cache
        self._llm_start_time:    Optional[float] = None
        self._cached_model_name: Optional[str]   = None

        # Streaming buffer
        self._stream_buffer: List[str] = []

    def as_list(self) -> List["AgentCallbackHandler"]:
        """Convenience — returns [self] for callbacks=handler.as_list()."""
        return [self]

    # ─────────────────────────────────────────────────────────────
    # START hooks
    # ─────────────────────────────────────────────────────────────

    def on_llm_start(
        self,
        serialized: Dict[str, Any],
        prompts: List[str],
        **kwargs: Any,
    ) -> None:
        """Fires for plain LLM classes (OpenAI base, etc.)"""
        self._reset_timing()
        self._cache_model_from_serialized(serialized)

    def on_chat_model_start(
        self,
        serialized: Dict[str, Any],
        messages: List[List[BaseMessage]],
        **kwargs: Any,
    ) -> None:
        """
        Fires for ChatOpenAI / ChatAnthropic / ChatGoogleGenerativeAI etc.
        on_llm_start does NOT fire for chat models — only this does.
        We cache the model name here because serialized is only available
        at start, not at on_chat_model_end.
        """
        self._reset_timing()
        self._cache_model_from_serialized(serialized)

    def _reset_timing(self) -> None:
        self._llm_start_time    = time.time()
        self._stream_buffer     = []
        self.latest_llm_content = None

    def _cache_model_from_serialized(self, serialized: Any) -> None:
        s  = _safe_dict(serialized)
        kw = _safe_dict(s.get("kwargs"))
        self._cached_model_name = (
            kw.get("model_name")
            or kw.get("model")
            or None
        )

    # ─────────────────────────────────────────────────────────────
    # STREAMING
    # ─────────────────────────────────────────────────────────────

    def on_llm_new_token(self, token: Union[str, Dict, List], **kwargs: Any) -> None:
        """
        Anthropic and Gemini often pass content chunks as complex objects
        rather than plain strings. We must normalize to string so that
        "".join(self._stream_buffer) doesn't TypeError.
        """
        if isinstance(token, str):
            self._stream_buffer.append(token)
        elif isinstance(token, dict):
            # Extract text if present (e.g. Anthropic block)
            t = token.get("text") or token.get("content") or ""
            if isinstance(t, str):
                self._stream_buffer.append(t)
        elif isinstance(token, list):
            # Flatten lists of strings or blocks
            for item in token:
                if isinstance(item, str):
                    self._stream_buffer.append(item)
                elif isinstance(item, dict):
                    t = item.get("text") or item.get("content") or ""
                    if isinstance(t, str):
                        self._stream_buffer.append(t)

    # ─────────────────────────────────────────────────────────────
    # END hooks
    # ─────────────────────────────────────────────────────────────

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        """Fires for plain LLM classes."""
        self._handle_llm_response(response, **kwargs)

    def on_chat_model_end(self, response: LLMResult, **kwargs: Any) -> None:
        """
        Fires for Chat Model classes (ChatOpenAI, ChatAnthropic, etc.)
        This was the originally missing method — without it the 📊 line
        never printed and usage was never captured.
        """
        self._handle_llm_response(response, **kwargs)

    # ─────────────────────────────────────────────────────────────
    # SHARED RESPONSE HANDLER
    # ─────────────────────────────────────────────────────────────

    def _handle_llm_response(self, response: LLMResult, **kwargs: Any) -> None:
        try:
            end_time    = time.time()
            start_time  = self._llm_start_time or end_time
            duration_ms = round((end_time - start_time) * 1000, 2)

            # ── 1. Content ─────────────────────────────────────
            content = (
                "".join(str(s) for s in self._stream_buffer)
                if self._stream_buffer
                else self._extract_content(response)
            )
            self.latest_llm_content = content

            # ── 2. Tokens ──────────────────────────────────────
            input_t, output_t = self._extract_tokens(response)

            # ── 3. Provider / model ────────────────────────────
            provider, model_name = self._extract_provider_model(response, kwargs)

            # ── 4. Finish reason ───────────────────────────────
            finish_reason = self._extract_finish_reason(response)

            # ── 5. Cost ────────────────────────────────────────
            cost = self._compute_cost(input_t, output_t, provider, model_name)

            # ── 6. Store ───────────────────────────────────────
            self.latest_llm_usage = {
                'input':        input_t,
                'output':       output_t,
                'total_tokens': input_t + output_t,
                'cost':         cost,
            }
            self.latest_llm_details = {
                'provider':        provider,
                'model_name':      model_name,
                'finish_reason':   finish_reason,
                'llm_start_time':  round(start_time, 3),
                'llm_end_time':    round(end_time, 3),
                'llm_duration_ms': duration_ms,
            }
            self.latest_llm_response = response

            # ── 7. Print ───────────────────────────────────────
            print(
                f"\n📊 {provider}:{model_name} | "
                f"I:{input_t} O:{output_t} | "
                f"${cost:.6f} | "
                f"{finish_reason} | "
                f"⏱️ {duration_ms}ms"
            )

            # ── 8. Debug dump (opt-in via debug=True) ──────────
            if self.debug:
                self._debug_dump(response, kwargs)

        except Exception as e:
            # Full traceback so we know the exact line that failed
            print(f"\n⚠️  AgentCallbackHandler error: {e}")
            traceback.print_exc()

    # ─────────────────────────────────────────────────────────────
    # AGENT HOOKS
    # ─────────────────────────────────────────────────────────────

    def on_agent_action(self, action: AgentAction, **kwargs: Any) -> Any:
        self._print_action(action)
        self._save(
            content    = action.log.strip(),
            tool_calls = [self._action_to_tool_call(action)],
        )
        self._reset()

    def on_agent_multi_action(self, actions: List[AgentAction], **kwargs: Any) -> Any:
        tool_calls, reasoning = [], ""
        for action in actions:
            self._print_action(action)
            tool_calls.append(self._action_to_tool_call(action))
            reasoning = reasoning or action.log.strip()
        self._save(content=reasoning, tool_calls=tool_calls)
        self._reset()

    def on_agent_finish(self, finish: AgentFinish, **kwargs: Any) -> Any:
        content = (
            self.latest_llm_content
            or finish.return_values.get("output", "")
        )
        self._save(content=content)
        self._reset()

    def on_chain_end(self, outputs: Dict[str, Any], **kwargs: Any) -> None:
        """
        Safety net: if an LLM was called directly inside a chain
        (no on_agent_action / on_agent_finish fired), flush pending data.
        """
        if self.latest_llm_usage and self.latest_llm_content:
            self._save(content=self.latest_llm_content)
            self._reset()

    # ─────────────────────────────────────────────────────────────
    # EXTRACTION HELPERS
    # ─────────────────────────────────────────────────────────────

    def _extract_content(self, response: LLMResult) -> str:
        try:
            gen = response.generations[0][0]
            if isinstance(gen, ChatGeneration):
                c = gen.message.content
                if isinstance(c, str):
                    return c
                if isinstance(c, list):
                    # Anthropic: content is a list of blocks when tool_use is involved
                    for block in c:
                        if isinstance(block, dict) and block.get("type") == "text":
                            return block.get("text", "")
                    return ""
            return getattr(gen, "text", "") or ""
        except (IndexError, AttributeError):
            return ""

    def _extract_tokens(self, response: LLMResult):
        """
        Checks in order:
        1. llm_output['token_usage']          — OpenAI
        2. llm_output['usage']                — some providers
        3. message.usage_metadata             — LangChain standard (Anthropic, OpenAI)
        4. message.response_metadata['usage'] — last fallback

        _safe_dict() wraps every value to prevent 'str has no .get()' errors.
        """
        llm_out = _safe_dict(getattr(response, "llm_output", {}))
        usage   = (
            _safe_dict(llm_out.get("token_usage"))
            or _safe_dict(llm_out.get("usage"))
            or {}
        )

        if not usage:
            try:
                msg        = response.generations[0][0].message
                usage_meta = getattr(msg, "usage_metadata", None)
                resp_meta  = _safe_dict(getattr(msg, "response_metadata", {}))
                usage      = (
                    _safe_dict(usage_meta)
                    or _safe_dict(resp_meta.get("usage"))
                    or {}
                )
            except (IndexError, AttributeError):
                usage = {}

        input_t  = usage.get("input_tokens",  usage.get("prompt_tokens",     0)) or 0
        output_t = usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
        return input_t, output_t

    def _extract_provider_model(self, response: LLMResult, kwargs: dict):
        """
        Priority:
        1. invocation_params in kwargs  — most reliable at runtime
        2. llm_output keys              — set by some wrappers
        3. response_metadata            — Anthropic / Gemini
        4. _cached_model_name           — cached in on_chat_model_start
        5. infer from model name string — last resort
        """
        llm_out    = _safe_dict(getattr(response, "llm_output", {}))
        inv_params = _safe_dict(kwargs.get("invocation_params"))

        model_name = (
            inv_params.get("model_name")
            or inv_params.get("model")
            or llm_out.get("model_name")
            or llm_out.get("model")
            or self._model_from_response_metadata(response)
            or self._cached_model_name
            or "unknown"
        )
        provider = (
            llm_out.get("provider")
            or self._infer_provider(model_name)
        )
        return provider, model_name

    def _model_from_response_metadata(self, response: LLMResult) -> Optional[str]:
        try:
            meta = _safe_dict(
                getattr(response.generations[0][0].message, "response_metadata", {})
            )
            return (
                meta.get("model")
                or meta.get("model_id")
                or meta.get("model_version")
            )
        except (IndexError, AttributeError):
            return None

    def _infer_provider(self, model_name: str) -> str:
        if not isinstance(model_name, str):
            return "unknown"
        m = model_name.lower()
        if any(k in m for k in ("gpt", "o1", "o3")):
            return "openai"
        if "claude" in m:
            return "anthropic"
        if "gemini" in m:
            return "google"
        return "unknown"

    def _extract_finish_reason(self, response: LLMResult) -> str:
        llm_out = _safe_dict(getattr(response, "llm_output", {}))
        if llm_out.get("finish_reason"):
            return llm_out["finish_reason"]
        try:
            meta = _safe_dict(
                getattr(response.generations[0][0].message, "response_metadata", {})
            )
            return (
                meta.get("stop_reason")       # Anthropic
                or meta.get("finish_reason")  # OpenAI / Gemini
                or "unknown"
            )
        except (IndexError, AttributeError):
            return "unknown"

    # ─────────────────────────────────────────────────────────────
    # COST / SAVE / RESET
    # ─────────────────────────────────────────────────────────────

    def _compute_cost(
        self, input_t: int, output_t: int, provider: str, model: str
    ) -> float:
        rates = PRICING.get(f"{provider}:{model}", (5.0, 15.0))
        return round((input_t * rates[0] + output_t * rates[1]) / 1_000_000, 6)

    def _action_to_tool_call(self, action: AgentAction) -> Dict[str, Any]:
        return {
            "type":  "tool_call",
            "id":    getattr(action, "tool_call_id"),  #, f"call_{id(action)}"),
            "name":  action.tool,
            "input": action.tool_input,
        }

    def _save(
        self,
        content: str,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        update_memory(
            role       = "assistant",
            content    = content,
            memory     = self.memory,
            tool_calls = tool_calls,
            usage      = self.latest_llm_usage,
            details    = self.latest_llm_details,
        )

    def _reset(self) -> None:
        self.latest_llm_usage    = None
        self.latest_llm_details  = None
        self.latest_llm_content  = None
        self.latest_llm_response = None
        self._llm_start_time     = None
        self._cached_model_name  = None
        self._stream_buffer      = []

    # ─────────────────────────────────────────────────────────────
    # PRINT
    # ─────────────────────────────────────────────────────────────

    def _print_action(self, action: AgentAction) -> None:
        input_str = (
            json.dumps(action.tool_input, indent=2)
            if isinstance(action.tool_input, dict)
            else str(action.tool_input)
        )
        lines = input_str.split("\n")

        print("\n╔" + "═" * 58 + "╗")
        print(f"║ {'🤖 ASSISTANT ACTION':^56} ║")
        print("╟" + "─" * 58 + "╢")
        print(f"║ 🛠️  TOOL  : {action.tool:<46} ║")
        for i, line in enumerate(lines):
            prefix       = "📥 INPUT : " if i == 0 else "           "
            display_line = line[:43] + "..." if len(line) > 46 else line
            print(f"║ {prefix}{display_line:<46} ║")

        if self.latest_llm_usage:
            u          = self.latest_llm_usage
            usage_info = f"I:{u['input']} O:{u['output']} T:{u['total_tokens']}"
            print("╟" + "─" * 58 + "╢")
            print(f"║ 📊 USAGE : {usage_info:<45} ║")

        print("╚" + "═" * 58 + "╝\n")

    def _debug_dump(self, response: LLMResult, kwargs: dict) -> None:
        print("\n🔥 " + "━" * 66 + " 🔥")
        print(f"┃ {'[DEBUG] FULL LLM RESULT':^62} ┃")
        print("━" * 70)
        try:
            llm_out = getattr(response, "llm_output", None)
            if llm_out:
                print("\n[llm_output]")
                pprint.pprint(llm_out, indent=2)
            gens = getattr(response, "generations", None)
            if gens and gens[0]:
                gen = gens[0][0]
                if hasattr(gen, "message"):
                    print("\n[message vars]")
                    pprint.pprint(vars(gen.message), indent=2, depth=4)
        except Exception as e:
            print(f"Debug dump error: {e}")
        print("🔥 " + "━" * 66 + " 🔥\n")