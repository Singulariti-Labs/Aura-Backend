from pydantic import BaseModel, Field
from typing import Optional, List, Union, Any
from langchain_core.tools import Tool

from app.Types.agent_types import Role, ROLE_TYPE
from app.LLM.memory import Message


class IMessage(BaseModel):
    """
    Represents a single message exchanged in a chat, supporting roles like user, assistant, system, and tool.
    Supports optional content, tool calls, image data, and utility methods for serialization and construction.
    """

    role: ROLE_TYPE = Field(...)  # type: ignore
    content: Optional[str] = Field(default=None)
    tool_calls: Optional[List[Tool]] = Field(default=None)
    name: Optional[str] = Field(default=None)
    tool_call_id: Optional[str] = Field(default=None)
    base64_image: Optional[str] = Field(default=None)

    def __add__(self, other) -> List["Message"]:
        """
        Allows adding a Message to another Message or a list of Messages, returning a new list.
        """
    
    def __radd__(self, other) -> List["Message"]:
        """
        Allows adding a Message to another Message or a list of Messages, returning a new list.
        """
    
    def to_dict(self) -> dict:
        """
        Converts the Message instance into a dictionary, including only the defined fields.
        Useful for serialization.
        """
    
    @classmethod
    def user_message(
        cls, content: str, base64_image: Optional[str] = None
    ) -> "Message":
        """
        Creates a user message with optional image input.

        Input:
        - content: Text content of the message.
        - base64_image: Optional image in base64 format.

        Returns:
        - A Message instance with role set to USER.
        """

    @classmethod
    def system_message(cls, content: str) -> "Message":
        """
        Creates a system message for providing system-level instructions to the model.

        Input:
        - content: Instruction or context as a string.

        Returns:
        - A Message instance with role set to SYSTEM.
        """
    
    @classmethod
    def assistant_message(
        cls, content: Optional[str] = None, base64_image: Optional[str] = None
    ) -> "Message":
        """
        Creates a message from the assistant, optionally containing an image.

        Input:
        - content: Assistant's reply content.
        - base64_image: Optional image in base64 format.

        Returns:
        - A Message instance with role set to ASSISTANT.
        """
    
    @classmethod
    def tool_message(
        cls, content: str, name, tool_call_id: str, base64_image: Optional[str] = None
    ) -> "Message":
        """
        Creates a tool message representing output from a tool execution.

        Input:
        - content: Tool output.
        - name: Tool name.
        - tool_call_id: Identifier linking to the triggering call.
        - base64_image: Optional image data.

        Returns:
        - A Message instance with role set to TOOL.
        """
    
    @classmethod
    def from_tool_calls(
        cls,
        tool_calls: List[Any],
        content: Union[str, List[str]] = "",
        base64_image: Optional[str] = None,
        **kwargs,
    ):
        """
        Constructs an assistant message from a list of raw tool calls, formatting them into expected structure.

        Input:
        - tool_calls: List of raw tool call objects from the LLM.
        - content: Optional message content.
        - base64_image: Optional base64 image.

        Returns:
        - A Message instance containing the tool_calls.
        """
    

class IMemory(BaseModel):
    """
    Handles storage and retrieval of chat messages, supporting memory limits and serialization.
    Acts as a simple message history manager.
    """

    messages: List[Message] = Field(default_factory=list)
    max_messages: int = Field(default=100)

    def add_message(self, message: Message) -> None:
        """
        Adds a single Message to memory and trims the list if it exceeds the maximum limit.
        """
    
    def add_messages(self, messages: List[Message]) -> None:
        """
        Adds multiple Messages to memory and trims the list if it exceeds the maximum limit.
        """
    
    def clear(self) -> None:
        """
        Clears all messages from memory.
        """
    
    def get_recent_messages(self, n: int) -> List[Message]:
        """
        Retrieves the most recent `n` messages from memory.

        Input:
        - n: Number of recent messages to fetch.

        Returns:
        - A list of the most recent Message objects.
        """ 
    
    
    def to_dict_list(self) -> List[dict]:
        """
        Converts all stored messages to a list of dictionaries for easy serialization or logging.

        Returns:
        - List of dictionaries representing messages.
        """