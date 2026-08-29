from __future__ import annotations

import json
import re
import threading
from typing import Any, Dict, Iterable

try:
    import tiktoken
except ImportError:  # The estimator remains usable in minimal deployments.
    tiktoken = None


MEDIA_TOKEN_ESTIMATES = {
    "image": 1024,
    "audio": 2048,
    "document": 4096,
}

_ENCODING = None
_ENCODING_INITIALIZED = False
_ENCODING_LOCK = threading.Lock()


class TokenCounter:
    """Stable cross-provider estimate used before native usage is available."""

    def __init__(self):
        global _ENCODING, _ENCODING_INITIALIZED
        if not _ENCODING_INITIALIZED:
            with _ENCODING_LOCK:
                if not _ENCODING_INITIALIZED:
                    if tiktoken is not None:
                        try:
                            # Some tiktoken installations lazily download this
                            # table. A failed lookup is cached process-wide so
                            # offline workers do not retry on every context.
                            _ENCODING = tiktoken.get_encoding("cl100k_base")
                        except Exception:
                            _ENCODING = None
                    _ENCODING_INITIALIZED = True
        self._encoding = _ENCODING

    def count_text(self, value: str) -> int:
        text = value or ""
        if self._encoding is not None:
            return len(self._encoding.encode(text))
        # Conservative deterministic fallback: words, punctuation and long
        # byte runs all contribute. Provider usage remains authoritative after
        # a real request.
        lexical = len(re.findall(r"\w+|[^\w\s]", text, flags=re.UNICODE))
        byte_estimate = (len(text.encode("utf-8")) + 3) // 4
        return max(lexical, byte_estimate)

    def count_block(self, block: Dict[str, Any]) -> int:
        block_type = str(block.get("type") or "text")
        if block_type == "text":
            return self.count_text(str(block.get("text") or "")) + 4
        if block_type == "tool_call":
            return self.count_text(
                json.dumps(
                    {
                        "name": block.get("name"),
                        "input": block.get("input") or {},
                    },
                    ensure_ascii=False,
                    default=str,
                )
            ) + 12
        return MEDIA_TOKEN_ESTIMATES.get(block_type, 256)

    def count_message(self, message: Dict[str, Any]) -> int:
        content = message.get("content", [])
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        return 8 + sum(
            self.count_block(block)
            for block in content
            if isinstance(block, dict)
        )

    def count_messages(self, messages: Iterable[Dict[str, Any]]) -> int:
        return sum(self.count_message(message) for message in messages)

    def count_tools(self, tools: Iterable[Dict[str, Any]]) -> int:
        return self.count_text(
            json.dumps(list(tools), ensure_ascii=False, default=str)
        )
