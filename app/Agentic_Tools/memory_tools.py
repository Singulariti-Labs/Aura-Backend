"""Client-side bridges for the file-based durable-memory tools.

Memory files are owned and modified by the connected client. This module only
transports validated tool inputs over the existing ``client_tool_request``
protocol, correlates the reply, records the request, and preserves the complete
client result for the language model.
"""

import json
from typing import Any, Dict, Optional, Sequence

from langchain_core.language_models.chat_models import BaseChatModel

from app.DB.Queries.agent_event import create_agent_event
from app.LLM.memory import Memory
from app.Task.task_manager import task_manager
from app.api.websocket_utils import send_ws_message
from app.helper import update_memory


class MemoryTools:
    """Expose durable-memory operations executed by the connected client."""

    def __init__(
        self,
        llm: BaseChatModel,
        task_id: str,
        chat_id: str,
        memory: Optional[Memory] = None,
    ) -> None:
        """Initialize the client bridge for one task and chat.

        Args:
            llm: Chat model associated with the agent using the memory tools.
            task_id: Identifier used to correlate client requests and responses.
            chat_id: Identifier of the chat that owns the memory files.
            memory: Optional conversation memory used to record tool activity.
        """

        self.llm = llm
        self.task_id = task_id
        self.chat_id = chat_id
        self.memory = memory
        self.task_state = task_manager.get_state(self.task_id)
        self.websocket = self.task_state.websocket
        self.dbpool = self.task_state.dbpool

    async def create_memory(
        self,
        *,
        name: str,
        target: str,
        description: str,
        aliases: Sequence[str],
        facts: Sequence[str],
        tool_call_id: Optional[str] = None,
    ) -> Any:
        """Create or completely rewrite one named durable-memory file.

        Unlike :meth:`memory_update`, this operation replaces the file's full
        metadata and fact collection. The connected client decides whether the
        operation is a first-time creation or a rewrite and returns the matching
        success message.

        Successful client results are returned as dictionaries. Failed client
        results are serialized in full as JSON strings, ensuring the model sees
        every structured diagnostic supplied by the client.

        Args:
            name: Memory filename without the ``.md`` suffix.
            target: Memory store in which the file belongs: ``user`` or ``memory``.
            description: Short description used to decide when to load the file.
            aliases: Alternative names used when retrieving the memory file.
            facts: Complete replacement list of durable facts for the file.
            tool_call_id: Model tool-call ID used to correlate the client response.

        Returns:
            The complete success result dictionary, or the complete failure result
            serialized as JSON.
        """

        # Copy caller-owned sequences into the exact JSON-compatible wire shape.
        tool_input: Dict[str, Any] = {
            "name": name,
            "target": target,
            "description": description,
            "aliases": list(aliases),
            "facts": list(facts),
        }

        try:
            # Delegate all file creation/rewrite and size enforcement to the client.
            await send_ws_message(
                websocket=self.websocket,
                type="client_tool_request",
                chat_id=self.chat_id,
                task_id=self.task_id,
                payload={
                    "tool": "create_memory",
                    "tool_call_id": tool_call_id,
                    "input": tool_input,
                    "coming_from": "create_memory_tool_func/server",
                },
            )

            # Store the exact client request for event replay and diagnostics.
            await create_agent_event(
                pool=self.dbpool,
                task_id=self.task_id,
                role="tool",
                message_type="client_tool_request",
                tool="create_memory",
                payload={
                    "tool_call_id": tool_call_id,
                    "input": tool_input,
                },
                seq=self.task_state.get_next_seq(),
            )

            # Correlate the response by call ID so parallel memory calls stay isolated.
            tool_response = await task_manager.wait_for_tool_response(
                self.task_id,
                tool_call_id,
            )
            response_type = tool_response.get("type")
            payload = tool_response.get("payload", {})

            if (
                response_type != "client_tool_response"
                or payload.get("tool") != "create_memory"
            ):
                return json.dumps(
                    {
                        "success": False,
                        "error": (
                            "Unexpected client tool response: "
                            f"type={response_type}, tool={payload.get('tool')}"
                        ),
                    },
                    ensure_ascii=False,
                )

            result = payload.get("result")
            if not isinstance(result, dict):
                return json.dumps(
                    {
                        "success": False,
                        "error": (
                            "Invalid create_memory result received from client."
                        ),
                    },
                    ensure_ascii=False,
                )

            serialized_result = json.dumps(result, ensure_ascii=False)

            # Keep the invocation and exact response available to later model turns.
            update_memory(
                role="assistant",
                content=(
                    "Creating or rewriting durable memory using create_memory "
                    f"with args {json.dumps(tool_input, ensure_ascii=False)}"
                ),
                memory=self.memory,
            )
            update_memory(
                role="tool",
                name="create_memory",
                tool_call_id=tool_call_id,
                content=serialized_result,
                memory=self.memory,
            )

            if result.get("success") is True:
                return result

            # Preserve every client error field instead of returning only ``error``.
            return serialized_result

        except Exception as error:
            return json.dumps(
                {
                    "success": False,
                    "error": f"Error executing create_memory: {error}",
                },
                ensure_ascii=False,
            )

    async def read_memory(
        self,
        *,
        name: str,
        target: str,
        tool_call_id: Optional[str] = None,
    ) -> str:
        """Read the complete contents of one relevant durable-memory file.

        The system prompt exposes only memory names, aliases, and descriptions.
        This function is the on-demand bridge used when that metadata indicates a
        particular file may contain context relevant to the current task. The
        client supplies the complete file without lifecycle timestamps.

        Both successful and failed result objects are returned as JSON strings,
        exactly preserving the client's ``content`` or structured error fields.

        Args:
            name: Memory filename to read without the ``.md`` suffix.
            target: Memory store to read from: ``user`` or ``memory``.
            tool_call_id: Model tool-call ID used to correlate the client response.

        Returns:
            A JSON string containing the complete client result object.
        """

        tool_input = {
            "name": name,
            "target": target,
        }

        try:
            # Ask the connected client to read the selected memory file in full.
            await send_ws_message(
                websocket=self.websocket,
                type="client_tool_request",
                chat_id=self.chat_id,
                task_id=self.task_id,
                payload={
                    "tool": "read_memory",
                    "tool_call_id": tool_call_id,
                    "input": tool_input,
                    "coming_from": "read_memory_tool_func/server",
                },
            )

            # Persist the exact request shape for replay and diagnostics.
            await create_agent_event(
                pool=self.dbpool,
                task_id=self.task_id,
                role="tool",
                message_type="client_tool_request",
                tool="read_memory",
                payload={
                    "tool_call_id": tool_call_id,
                    "input": tool_input,
                },
                seq=self.task_state.get_next_seq(),
            )

            # Correlate parallel reads by their model-provided tool-call IDs.
            tool_response = await task_manager.wait_for_tool_response(
                self.task_id,
                tool_call_id,
            )
            response_type = tool_response.get("type")
            payload = tool_response.get("payload", {})

            if (
                response_type != "client_tool_response"
                or payload.get("tool") != "read_memory"
            ):
                return json.dumps(
                    {
                        "success": False,
                        "error": (
                            "Unexpected client tool response: "
                            f"type={response_type}, tool={payload.get('tool')}"
                        ),
                    },
                    ensure_ascii=False,
                )

            result = payload.get("result")
            if not isinstance(result, dict):
                return json.dumps(
                    {
                        "success": False,
                        "error": "Invalid read_memory result received from client.",
                    },
                    ensure_ascii=False,
                )

            # The result is never reduced to content/error alone. This retains any
            # additional metadata added to the client contract in the future.
            serialized_result = json.dumps(result, ensure_ascii=False)

            update_memory(
                role="assistant",
                content=(
                    "Reading durable memory using read_memory with args "
                    f"{json.dumps(tool_input, ensure_ascii=False)}"
                ),
                memory=self.memory,
            )
            update_memory(
                role="tool",
                name="read_memory",
                tool_call_id=tool_call_id,
                content=serialized_result,
                memory=self.memory,
            )

            return serialized_result

        except Exception as error:
            return json.dumps(
                {
                    "success": False,
                    "error": f"Error executing read_memory: {error}",
                },
                ensure_ascii=False,
            )

    async def memory_update(
        self,
        *,
        name: str,
        target: str,
        action: Optional[str] = None,
        description: Optional[str] = None,
        content: Optional[str] = None,
        new_text: Optional[str] = None,
        old_text: Optional[str] = None,
        operations: Optional[Sequence[Dict[str, Any]]] = None,
        tool_call_id: Optional[str] = None,
    ) -> Any:
        """Ask the client to update a named durable-memory file.

        The client owns file locking, atomic batch application, fact matching,
        size-limit enforcement, and drift backups. Optional values that were not
        supplied are omitted from the wire payload so the client can distinguish
        an absent property from an explicit empty value.

        Successful client results are returned as dictionaries. Failed client
        results are returned as JSON strings containing the *entire* result object
        so fields such as ``current_entries``, ``usage``, ``drift_backup``, and
        ``remediation`` are never discarded.

        Args:
            name: Memory filename without the ``.md`` suffix.
            target: Memory store to update: ``memory`` or ``user``.
            action: Single-operation action: add, replace, or remove.
            description: Optional replacement description for the memory file.
            content: Preferred entry content for add or replace.
            new_text: Alias for ``content``; content wins client-side when both exist.
            old_text: Unique substring identifying an entry to replace or remove.
            operations: Optional list of operations applied atomically by the client.
            tool_call_id: Model tool-call ID used to correlate the client response.

        Returns:
            The complete success result dictionary, or the complete failure result
            serialized as JSON.
        """

        tool_input: Dict[str, Any] = {
            "name": name,
            "target": target,
        }
        optional_values = {
            "action": action,
            "description": description,
            "content": content,
            "new_text": new_text,
            "old_text": old_text,
            "operations": list(operations) if operations is not None else None,
        }
        tool_input.update(
            {
                key: value
                for key, value in optional_values.items()
                if value is not None
            }
        )

        try:
            # Send the exact memory operation to the client-side file store.
            await send_ws_message(
                websocket=self.websocket,
                type="client_tool_request",
                chat_id=self.chat_id,
                task_id=self.task_id,
                payload={
                    "tool": "memory_update",
                    "tool_call_id": tool_call_id,
                    "input": tool_input,
                    "coming_from": "memory_update_tool_func/server",
                },
            )

            # Persist the same request shape for replay and debugging.
            await create_agent_event(
                pool=self.dbpool,
                task_id=self.task_id,
                role="tool",
                message_type="client_tool_request",
                tool="memory_update",
                payload={
                    "tool_call_id": tool_call_id,
                    "input": tool_input,
                },
                seq=self.task_state.get_next_seq(),
            )

            # The task manager isolates concurrent calls by their tool_call_id.
            tool_response = await task_manager.wait_for_tool_response(
                self.task_id,
                tool_call_id,
            )
            response_type = tool_response.get("type")
            payload = tool_response.get("payload", {})

            if (
                response_type != "client_tool_response"
                or payload.get("tool") != "memory_update"
            ):
                return json.dumps(
                    {
                        "success": False,
                        "done": True,
                        "target": target,
                        "error": (
                            "Unexpected client tool response: "
                            f"type={response_type}, tool={payload.get('tool')}"
                        ),
                    },
                    ensure_ascii=False,
                )

            result = payload.get("result")
            if not isinstance(result, dict):
                return json.dumps(
                    {
                        "success": False,
                        "done": True,
                        "target": target,
                        "error": (
                            "Invalid memory_update result received from client."
                        ),
                    },
                    ensure_ascii=False,
                )

            serialized_result = json.dumps(result, ensure_ascii=False)

            # Record the invocation and exact client result in conversation memory.
            update_memory(
                role="assistant",
                content=(
                    "Updating durable memory using memory_update with args "
                    f"{json.dumps(tool_input, ensure_ascii=False)}"
                ),
                memory=self.memory,
            )
            update_memory(
                role="tool",
                name="memory_update",
                tool_call_id=tool_call_id,
                content=serialized_result,
                memory=self.memory,
            )

            if result.get("success") is True:
                return result

            # Do not reduce a rich client failure to only its error message.
            return serialized_result

        except Exception as error:
            return json.dumps(
                {
                    "success": False,
                    "done": True,
                    "target": target,
                    "error": f"Error executing memory_update: {error}",
                },
                ensure_ascii=False,
            )
