from abc import ABC, abstractmethod
from contextvars import ContextVar
from typing import Any, Optional, Type, Union
from langchain_core.tools import StructuredTool
from pydantic import BaseModel as LCBaseModel

from app.LLM.memory import Memory


_current_tool_call_id: ContextVar[Optional[str]] = ContextVar(
    "current_tool_call_id",
    default=None,
)
_current_tool_input: ContextVar[Optional[dict]] = ContextVar(
    "current_tool_input",
    default=None,
)


def get_current_tool_call_id() -> Optional[str]:
    """Return the LangChain tool call id for the currently running tool."""
    return _current_tool_call_id.get()


def get_current_tool_input() -> Optional[dict]:
    """Return the parsed input for the currently running tool."""
    return _current_tool_input.get()


class AuraStructuredTool(StructuredTool):
    """StructuredTool variant that forwards LangChain's runtime tool_call_id."""

    def _to_args_and_kwargs(
        self,
        tool_input: Union[str, dict],
        tool_call_id: Optional[str],
    ) -> tuple[tuple, dict]:
        tool_args, tool_kwargs = super()._to_args_and_kwargs(
            tool_input,
            tool_call_id,
        )
        if tool_call_id is not None:
            tool_kwargs["_tool_call_id"] = tool_call_id
        return tool_args, tool_kwargs


class BaseTool(ABC):
    """
    Abstract base class for creating custom tools that can be integrated into a language model agent framework.

    Each tool has:
    - a name (for identification),
    - a description (for LLMs or users to understand its purpose),
    - an optional memory (to keep track of previous interactions or context).

    Subclasses must implement the `run` method to define the tool's core functionality.

    The `to_tool` method converts the tool into a Langchain-compatible `Tool` object for execution by LangChain agents.
    """
    def __init__(self, name: str, description: str, memory: Optional[Memory] = None,  args_schema: Optional[Type[LCBaseModel]] = None):
        """
        Initializes the tool with a name, description, and optional memory context.

        Input:
        - name: The name of the tool.
        - description: A brief explanation of what the tool does.
        - memory: Optional memory instance to retain or access conversation history.
        """
        self.name = name
        self.description = description
        self.memory = memory
        self.args_schema = args_schema

    @abstractmethod
    def run(self, inputs: LCBaseModel) -> Any:
        """
        Abstract method to be implemented by subclasses with the aubagent/tool’s execution logic/ method to invoke the subagent/tool's.

        Input:
        - inputs: A dictionary of input parameters required for the tool.

        Returns:
        - Any result based on the tool’s logic.

        Raises:
        - NotImplementedError if not overridden in a subclass.
        """
        pass

    async def _wrapped_func(
        self,
        _tool_call_id: Optional[str] = None,
        **kwargs,
    ) -> Any:
        """
        Converts dict inputs to a Pydantic schema and calls `run`.
        This is the function passed to `StructuredTool`.
        """
        if self.args_schema:
            inputs = self.args_schema(**kwargs)
        else:
            raise ValueError("args_schema must be provided to use StructuredTool.")

        id_token = _current_tool_call_id.set(_tool_call_id)
        input_token = _current_tool_input.set(dict(kwargs))
        try:
            return await self.run(inputs)
        finally:
            _current_tool_input.reset(input_token)
            _current_tool_call_id.reset(id_token)

    def to_tool(self) -> StructuredTool:
        """
        Converts the BaseTool instance into a Langchain `Tool` for integration with LangChain agents.

        Returns:
        - LangchainTool: A callable tool object usable in LangChain workflows.
        """
        def dummy_func(_):
                raise NotImplementedError("This tool uses an async method. Use `coroutine` instead.")
        
        return AuraStructuredTool.from_function(
            name=self.name,
            description=self.description,
            func=dummy_func,
            coroutine=self._wrapped_func,
            args_schema=self.args_schema
        )
