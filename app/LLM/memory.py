from pydantic import BaseModel, Field
from typing import Optional, List, Union, Any
from langchain_core.tools import Tool

from app.Types.agent_types import Role, ROLE_TYPE


class Message(BaseModel):
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
        if isinstance(other, list):
            return [self] + other
        elif isinstance(other, Message):
            return [self, other]
        else:
            raise TypeError(
                f"unsupported operand type(s) for +: '{type(self).__name__}' and '{type(other).__name__}'"
            )

    def __radd__(self, other) -> List["Message"]:
        """
        Allows adding a Message to another Message or a list of Messages, returning a new list.
        """
        if isinstance(other, list):
            return other + [self]
        else:
            raise TypeError(
                f"unsupported operand type(s) for +: '{type(other).__name__}' and '{type(self).__name__}'"
            )

    def to_dict(self) -> dict:
        """
        Converts the Message instance into a dictionary, including only the defined fields.
        Useful for serialization.
        """
        message = {"role": self.role}
        if self.content is not None:
            message["content"] = self.content
        if self.tool_calls is not None:
            message["tool_calls"] = [tool_call.dict() for tool_call in self.tool_calls]
        if self.name is not None:
            message["name"] = self.name
        if self.tool_call_id is not None:
            message["tool_call_id"] = self.tool_call_id
        if self.base64_image is not None:
            message["base64_image"] = self.base64_image
        return message

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
        return cls(role=Role.USER, content=content, base64_image=base64_image)

    @classmethod
    def system_message(cls, content: str) -> "Message":
        """
        Creates a system message for providing system-level instructions to the model.

        Input:
        - content: Instruction or context as a string.

        Returns:
        - A Message instance with role set to SYSTEM.
        """
        return cls(role=Role.SYSTEM, content=content)

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
        return cls(role=Role.ASSISTANT, content=content, base64_image=base64_image)

    @classmethod
    def tool_message(
        cls, content: str, name, tool_call_id: Optional[str] = None, base64_image: Optional[str] = None
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
        return cls(
            role=Role.TOOL,
            content=content,
            name=name,
            tool_call_id=tool_call_id,
            base64_image=base64_image,
        )

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
        formatted_calls = [
            {"id": call.id, "function": call.function.model_dump(), "type": "function"}
            for call in tool_calls
        ]
        return cls(
            role=Role.ASSISTANT,
            content=content,
            tool_calls=formatted_calls,
            base64_image=base64_image,
            **kwargs,
        )

class Memory(BaseModel):
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
        self.messages.append(message)
        # Optional: Implement message limit
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[-self.max_messages :]

    def add_messages(self, messages: List[Message]) -> None:
        """
        Adds multiple Messages to memory and trims the list if it exceeds the maximum limit.
        """
        self.messages.extend(messages)
        # Optional: Implement message limit
        if len(self.messages) > self.max_messages:
            self.messages = self.messages[-self.max_messages :]

    def clear(self) -> None:
        """
        Clears all messages from memory.
        """
        self.messages.clear()

    def get_recent_messages(self, n: int) -> List[Message]:
        """
        Retrieves the most recent `n` messages from memory.

        Input:
        - n: Number of recent messages to fetch.

        Returns:
        - A list of the most recent Message objects.
        """        
        return self.messages[-n:]

    def to_dict_list(self) -> List[dict]:
        """
        Converts all stored messages to a list of dictionaries for easy serialization or logging.

        Returns:
        - List of dictionaries representing messages.
        """
        return [msg.to_dict() for msg in self.messages]
