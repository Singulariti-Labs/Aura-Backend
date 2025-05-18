from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Type
from langchain_core.tools import StructuredTool
from pydantic import BaseModel as LCBaseModel

from app.LLM.memory import Memory

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

    async def _wrapped_func(self, **kwargs) -> Any:
        """
        Converts dict inputs to a Pydantic schema and calls `run`.
        This is the function passed to `StructuredTool`.
        """
        if self.args_schema:
            inputs = self.args_schema(**kwargs)
        else:
            raise ValueError("args_schema must be provided to use StructuredTool.")
        return await self.run(inputs)

    def to_tool(self) -> StructuredTool:
        """
        Converts the BaseTool instance into a Langchain `Tool` for integration with LangChain agents.

        Returns:
        - LangchainTool: A callable tool object usable in LangChain workflows.
        """
        def dummy_func(_):
                raise NotImplementedError("This tool uses an async method. Use `coroutine` instead.")
        
        return StructuredTool.from_function(
            name=self.name,
            description=self.description,
            func=dummy_func,
            coroutine=self._wrapped_func,
            args_schema=self.args_schema
        )