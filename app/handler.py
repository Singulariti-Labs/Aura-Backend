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
from app.RateLimit.rate_limit_service import schedule_token_usage_update
from app.RateLimit.token_pricing import calculate_token_cost_usd_float
from app.LLM.model_token_limits import MAX_OUTPUT_TOKEN_LIMIT_MESSAGE
from app.helper import update_memory


def _safe_dict(value: Any) -> dict:
    """
    Normalizes provider/LangChain metadata to a dict.

    Providers do not always return plain dictionaries. Anthropic usage can
    arrive as an SDK/Pydantic object, while OpenAI and Gemini usually arrive
    as dict-like metadata. Keeping this tolerant prevents valid token usage
    from being dropped before extraction.
    """
    if isinstance(value, dict):
        return value
    if value is None or isinstance(value, (str, bytes)):
        return {}
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump()
            return dumped if isinstance(dumped, dict) else {}
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            dumped = value.dict()
            return dumped if isinstance(dumped, dict) else {}
        except Exception:
            pass
    if hasattr(value, "_asdict"):
        try:
            dumped = value._asdict()
            return dumped if isinstance(dumped, dict) else {}
        except Exception:
            pass
    known_keys = (
        "input_tokens",
        "output_tokens",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "prompt_token_count",
        "candidates_token_count",
        "total_token_count",
        "input_token_count",
        "output_token_count",
    )
    attr_values = {
        key: getattr(value, key)
        for key in known_keys
        if hasattr(value, key) and getattr(value, key) is not None
    }
    if attr_values:
        return attr_values
    if hasattr(value, "__dict__"):
        return {
            key: val
            for key, val in vars(value).items()
            if not key.startswith("_") and not callable(val)
        }
    return {}


class MaxOutputTokenLimitError(RuntimeError):
    def __init__(
        self,
        *,
        details: Optional[Dict[str, Any]] = None,
        usage: Optional[Dict[str, Any]] = None,
    ):
        self.error_body = {
            "message": MAX_OUTPUT_TOKEN_LIMIT_MESSAGE,
            "stop_reason": "max_tokens",
            "details": details or {},
            "usage": usage or {},
        }
        super().__init__(json.dumps(self.error_body, default=str))


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

    def __init__(
        self,
        memory: Memory,
        debug: bool = False,
        rate_limit_pool: Optional[Any] = None,
        user_id: Optional[str] = None,
        fallback_provider: Optional[str] = None,
        fallback_model_name: Optional[str] = None,
        rate_limit_loop: Optional[Any] = None,
    ):
        super().__init__()
        self.memory = memory
        self.debug  = debug
        self.rate_limit_pool = rate_limit_pool
        self.user_id = user_id
        self.rate_limit_loop = rate_limit_loop
        self.fallback_provider = fallback_provider
        self.fallback_model_name = fallback_model_name

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
        self._last_tool_call_batch_key: Optional[tuple[str, ...]] = None

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

    def _raise_if_max_tokens_stop(self) -> None:
        details = self.latest_llm_details or {}
        finish_reason = str(details.get("finish_reason") or "").lower()
        if finish_reason not in {"max_tokens", "length"}:
            return

        raise MaxOutputTokenLimitError(
            details=details,
            usage=self.latest_llm_usage,
        )

    def on_agent_action(self, action: AgentAction, **kwargs: Any) -> Any:
        self._capture_from_agent_action(action, kwargs)
        self._raise_if_max_tokens_stop()
        self._print_action(action)
        self._save_tool_call_batch(
            content=action.log.strip(),
            tool_calls=(
                self._tool_calls_from_action_context(action)
                or [self._action_to_tool_call(action)]
            ),
        )

    def on_agent_multi_action(self, actions: List[AgentAction], **kwargs: Any) -> Any:
        tool_calls, reasoning = [], ""
        for action in actions:
            self._capture_from_agent_action(action, kwargs)
            self._raise_if_max_tokens_stop()
            self._print_action(action)
            tool_calls.append(self._action_to_tool_call(action))
            reasoning = reasoning or action.log.strip()
        if actions:
            tool_calls = self._tool_calls_from_action_context(actions[0]) or tool_calls
        self._save_tool_call_batch(content=reasoning, tool_calls=tool_calls)

    def on_agent_finish(self, finish: AgentFinish, **kwargs: Any) -> Any:
        self._raise_if_max_tokens_stop()
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
        self._raise_if_max_tokens_stop()
        if self.latest_llm_usage and self.latest_llm_content:
            self._save(content=self.latest_llm_content)
            self._reset()

    # ─────────────────────────────────────────────────────────────
    # EXTRACTION HELPERS
    # ─────────────────────────────────────────────────────────────

    def _capture_from_agent_action(
        self, action: AgentAction, kwargs: dict
    ) -> None:
        """
        Tool-calling agents can expose the model response on action.message_log.
        For Anthropic, the agent action may be saved before latest_llm_usage is
        populated from the LLM callback, so recover usage/details from there.
        """
        if self.latest_llm_usage and self.latest_llm_details:
            return

        messages = getattr(action, "message_log", None) or []
        for msg in reversed(list(messages)):
            input_t, output_t = self._extract_tokens_from_message(msg)
            finish_reason = self._extract_finish_reason_from_message(msg)
            is_output_limit = str(finish_reason or "").lower() in {"max_tokens", "length"}
            if not (input_t or output_t or is_output_limit):
                continue

            end_time = time.time()
            start_time = self._llm_start_time or end_time
            duration_ms = round((end_time - start_time) * 1000, 2)
            provider, model_name = self._extract_provider_model_from_message(
                msg, kwargs
            )
            cost = self._compute_cost(input_t, output_t, provider, model_name)

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
            if not self.latest_llm_content:
                self.latest_llm_content = self._extract_content_from_message(msg)
            return

    def _extract_content(self, response: LLMResult) -> str:
        try:
            gen = response.generations[0][0]
            if isinstance(gen, ChatGeneration):
                return self._extract_content_from_message(gen.message)
            return getattr(gen, "text", "") or ""
        except (IndexError, AttributeError):
            return ""

    def _extract_content_from_message(self, msg: Any) -> str:
        c = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            # Anthropic: content is a list of blocks when tool_use is involved
            for block in c:
                if isinstance(block, dict) and block.get("type") == "text":
                    return block.get("text", "")
            return ""
        return ""

    def _extract_tokens(self, response: LLMResult):
        """
        Checks in order:
        1. llm_output['token_usage']          — OpenAI
        2. llm_output['usage']                — some providers
        3. message.usage_metadata             — LangChain standard (Anthropic, OpenAI)
        4. message.response_metadata['usage'] — last fallback

        _safe_dict() normalizes provider metadata before key lookup.
        """
        llm_out = _safe_dict(getattr(response, "llm_output", {}))
        candidates = [
            llm_out.get("token_usage"),
            llm_out.get("usage"),
            llm_out.get("usage_metadata"),
        ]

        try:
            gen = response.generations[0][0]
            msg = getattr(gen, "message", None)
            if msg is not None:
                resp_meta = _safe_dict(getattr(msg, "response_metadata", {}))
                add_kwargs = _safe_dict(getattr(msg, "additional_kwargs", {}))
                candidates.extend([
                    getattr(msg, "usage_metadata", None),
                    resp_meta.get("usage_metadata"),
                    resp_meta.get("token_usage"),
                    resp_meta.get("usage"),
                    add_kwargs.get("usage_metadata"),
                    add_kwargs.get("token_usage"),
                    add_kwargs.get("usage"),
                ])
            gen_info = _safe_dict(getattr(gen, "generation_info", {}))
            candidates.extend([
                gen_info.get("usage_metadata"),
                gen_info.get("token_usage"),
                gen_info.get("usage"),
            ])
        except (IndexError, AttributeError):
            pass

        return self._tokens_from_candidates(candidates)

    def _extract_tokens_from_message(self, msg: Any):
        resp_meta = _safe_dict(
            msg.get("response_metadata")
            if isinstance(msg, dict)
            else getattr(msg, "response_metadata", {})
        )
        add_kwargs = _safe_dict(
            msg.get("additional_kwargs")
            if isinstance(msg, dict)
            else getattr(msg, "additional_kwargs", {})
        )
        usage_meta = (
            msg.get("usage_metadata")
            if isinstance(msg, dict)
            else getattr(msg, "usage_metadata", None)
        )

        candidates = [
            usage_meta,
            resp_meta.get("usage_metadata"),
            resp_meta.get("token_usage"),
            resp_meta.get("usage"),
            add_kwargs.get("usage_metadata"),
            add_kwargs.get("token_usage"),
            add_kwargs.get("usage"),
        ]
        return self._tokens_from_candidates(candidates)

    def _tokens_from_candidates(self, candidates: List[Any]):
        for candidate in candidates:
            usage = _safe_dict(candidate)
            input_t = self._token_value(
                usage,
                "input_tokens",
                "prompt_tokens",
                "prompt_token_count",
                "input_token_count",
                "inputTokens",
                "promptTokens",
            )
            output_t = self._token_value(
                usage,
                "output_tokens",
                "completion_tokens",
                "candidates_token_count",
                "output_token_count",
                "outputTokens",
                "completionTokens",
            )

            if input_t or output_t:
                return input_t, output_t

            total_t = self._token_value(
                usage,
                "total_tokens",
                "total_token_count",
                "totalTokens",
            )
            if total_t:
                return total_t, 0

        return 0, 0

    def _token_value(self, usage: dict, *keys: str) -> int:
        for key in keys:
            value = usage.get(key)
            if value is None:
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
        return 0

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
            or self.fallback_model_name
            or "unknown"
        )
        inferred_provider = self._infer_provider(model_name)
        provider = (
            llm_out.get("provider")
            or (inferred_provider if inferred_provider != "unknown" else None)
            or self.fallback_provider
            or inferred_provider
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

    def _extract_provider_model_from_message(self, msg: Any, kwargs: dict):
        inv_params = _safe_dict(kwargs.get("invocation_params"))
        resp_meta = _safe_dict(
            msg.get("response_metadata")
            if isinstance(msg, dict)
            else getattr(msg, "response_metadata", {})
        )
        add_kwargs = _safe_dict(
            msg.get("additional_kwargs")
            if isinstance(msg, dict)
            else getattr(msg, "additional_kwargs", {})
        )

        model_name = (
            inv_params.get("model_name")
            or inv_params.get("model")
            or resp_meta.get("model")
            or resp_meta.get("model_name")
            or resp_meta.get("model_id")
            or resp_meta.get("model_version")
            or add_kwargs.get("model")
            or add_kwargs.get("model_name")
            or self._cached_model_name
            or self.fallback_model_name
            or "unknown"
        )
        inferred_provider = self._infer_provider(model_name)
        provider = (
            resp_meta.get("model_provider")
            or add_kwargs.get("provider")
            or (inferred_provider if inferred_provider != "unknown" else None)
            or self.fallback_provider
            or inferred_provider
        )
        return provider, model_name

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

    def _extract_finish_reason_from_message(self, msg: Any) -> str:
        meta = _safe_dict(
            msg.get("response_metadata")
            if isinstance(msg, dict)
            else getattr(msg, "response_metadata", {})
        )
        return (
            meta.get("stop_reason")       # Anthropic
            or meta.get("finish_reason")  # OpenAI / Gemini
            or "unknown"
        )

    # ─────────────────────────────────────────────────────────────
    # COST / SAVE / RESET
    # ─────────────────────────────────────────────────────────────

    def _compute_cost(
        self, input_t: int, output_t: int, provider: str, model: str
    ) -> float:
        return calculate_token_cost_usd_float(
            provider=provider,
            model_name=model,
            input_tokens=input_t,
            output_tokens=output_t,
        )

    def _tool_calls_from_action_context(
        self,
        action: AgentAction,
    ) -> List[Dict[str, Any]]:
        """
        Recover the full tool-call batch from the AI message that produced an action.

        LangChain may call on_agent_action once per action even when the model
        emitted multiple parallel tool calls. Each action still usually carries
        the original AIMessage in message_log, where all sibling tool calls live.
        """
        messages = getattr(action, "message_log", None) or []
        for msg in reversed(list(messages)):
            tool_calls: List[Dict[str, Any]] = []

            raw_tool_calls = getattr(msg, "tool_calls", None)
            if raw_tool_calls:
                tool_calls.extend(
                    tc
                    for tc in (
                        self._normalize_tool_call(raw_call)
                        for raw_call in raw_tool_calls
                    )
                    if tc
                )

            additional_kwargs = _safe_dict(getattr(msg, "additional_kwargs", {}))
            extra_tool_calls = additional_kwargs.get("tool_calls")
            if extra_tool_calls:
                tool_calls.extend(
                    tc
                    for tc in (
                        self._normalize_tool_call(raw_call)
                        for raw_call in extra_tool_calls
                    )
                    if tc
                )

            content = getattr(msg, "content", None)
            if isinstance(content, list):
                tool_calls.extend(
                    tc
                    for tc in (
                        self._normalize_tool_call(block)
                        for block in content
                        if isinstance(block, dict)
                        and block.get("type") in ("tool_call", "tool_use")
                    )
                    if tc
                )

            tool_calls = self._dedupe_tool_calls(tool_calls)
            if tool_calls:
                return tool_calls

        return []

    def _action_to_tool_call(self, action: AgentAction) -> Dict[str, Any]:
        tool_call_id = getattr(action, "tool_call_id", None) or getattr(action, "id", None)
        return {
            "type":         "tool_call",
            "id":           tool_call_id,
            "tool_call_id": tool_call_id,
            "name":         action.tool,
            "input": action.tool_input,
        }

    def _normalize_tool_call(self, raw_call: Any) -> Optional[Dict[str, Any]]:
        if isinstance(raw_call, dict):
            if "function" in raw_call:
                function = _safe_dict(raw_call.get("function"))
                tool_input = function.get("arguments", {})
                if isinstance(tool_input, str):
                    try:
                        tool_input = json.loads(tool_input)
                    except json.JSONDecodeError:
                        pass
                tool_call_id = raw_call.get("id") or raw_call.get("tool_call_id")
                name = function.get("name")
            else:
                tool_input = (
                    raw_call.get("input")
                    if "input" in raw_call
                    else raw_call.get("args", raw_call.get("arguments", {}))
                )
                if not tool_input and raw_call.get("partial_json"):
                    try:
                        tool_input = json.loads(raw_call["partial_json"])
                    except (TypeError, json.JSONDecodeError):
                        pass
                tool_call_id = raw_call.get("tool_call_id") or raw_call.get("id")
                name = raw_call.get("name")
        else:
            tool_input = (
                getattr(raw_call, "input", None)
                if hasattr(raw_call, "input")
                else getattr(raw_call, "args", getattr(raw_call, "arguments", {}))
            )
            tool_call_id = (
                getattr(raw_call, "tool_call_id", None)
                or getattr(raw_call, "id", None)
            )
            name = getattr(raw_call, "name", None)

        if not tool_call_id and not name:
            return None

        return {
            "type":         "tool_call",
            "id":           tool_call_id,
            "tool_call_id": tool_call_id,
            "name":         name,
            "input":        tool_input or {},
        }

    def _dedupe_tool_calls(
        self,
        tool_calls: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        seen = set()
        deduped = []
        for tool_call in tool_calls:
            key = self._tool_call_key(tool_call)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(tool_call)
        return deduped

    def _tool_call_key(self, tool_call: Dict[str, Any]) -> str:
        tool_call_id = tool_call.get("tool_call_id") or tool_call.get("id")
        if tool_call_id:
            return str(tool_call_id)
        try:
            input_key = json.dumps(
                tool_call.get("input", {}),
                sort_keys=True,
                default=str,
            )
        except TypeError:
            input_key = str(tool_call.get("input", {}))
        return f"{tool_call.get('name')}:{input_key}"

    def _tool_call_batch_key(
        self,
        tool_calls: List[Dict[str, Any]],
    ) -> tuple[str, ...]:
        batch_key = []
        for tool_call in tool_calls:
            tool_call_id = tool_call.get("tool_call_id") or tool_call.get("id")
            if not tool_call_id:
                return ()
            batch_key.append(str(tool_call_id))
        return tuple(batch_key)

    def _save_tool_call_batch(
        self,
        content: str,
        tool_calls: List[Dict[str, Any]],
    ) -> None:
        batch_key = self._tool_call_batch_key(tool_calls)
        if batch_key and batch_key == self._last_tool_call_batch_key:
            self._reset()
            return

        self._save(content=content, tool_calls=tool_calls)
        if batch_key:
            self._last_tool_call_batch_key = batch_key
        self._reset()

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
        schedule_token_usage_update(
            pool=self.rate_limit_pool,
            user_id=self.user_id,
            usage=self.latest_llm_usage,
            details=self.latest_llm_details,
            event_loop=self.rate_limit_loop,
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
