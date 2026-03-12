from typing import List, Dict, Any, Union
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, BaseMessage
import json

def _format_content_blocks(content: Union[str, List[Dict[str, Any]]], provider: str) -> Union[str, List[Dict[str, Any]]]:
    if isinstance(content, str):
        return content
        
    formatted_blocks = []
    
    for block in content:
        block_type = block.get("type")
        
        if block_type == "text":
            formatted_blocks.append({"type": "text", "text": block.get("text", "")})
            
        elif block_type == "image":
            source_type = block.get("source_type")
            url = block.get("url")
            
            if source_type == "base64":
                data = block.get("data")
                mime_type = block.get("mime_type", "image/png")
                url = f"data:{mime_type};base64,{data}"
                
            img_dict = {
                "type": "image_url",
                "image_url": {"url": url}
            }
            if provider == "openai" and "detail" in block:
                img_dict["image_url"]["detail"] = block["detail"]
                
            formatted_blocks.append(img_dict)

        elif block_type == "audio":
            source_type = block.get("source_type")
            if source_type == "base64":
                data = block.get("data")
                mime_type = block.get("mime_type", "audio/mp3")
                url = f"data:{mime_type};base64,{data}"
            else:
                url = block.get("url")
                
            formatted_blocks.append({
                "type": "audio_url",
                "audio_url": {"url": url}
            })

        elif block_type == "video":
            source_type = block.get("source_type")
            if source_type == "base64":
                data = block.get("data")
                mime_type = block.get("mime_type", "video/mp4")
                url = f"data:{mime_type};base64,{data}"
            else:
                url = block.get("url")
                
            formatted_blocks.append({
                "type": "video_url",
                "video_url": {"url": url}
            })

        elif block_type == "file":
            # Treat as image for native binary encoding, common for LangChain standard fallback
            source_type = block.get("source_type")
            if source_type == "base64":
                data = block.get("data")
                mime_type = block.get("mime_type", "application/pdf")
                url = f"data:{mime_type};base64,{data}"
            else:
                url = block.get("url")

            file_dict = {
                "type": "image_url",
                "image_url": {"url": url}
            }
            if provider == "openai" and "filename" in block:
                file_dict["image_url"]["filename"] = block["filename"]
                
            formatted_blocks.append(file_dict)
            
        else:
            # Pass unknown or already correctly formatted blocks natively
            formatted_blocks.append(block)
            
    return formatted_blocks


def format_to_langchain(history: List[Dict[str, Any]], provider: str = "generic") -> List[BaseMessage]:
    """
    Converts a list of custom message dictionaries into LangChain BaseMessage objects.
    Capable of handling multimodal content including text, images, audio, and files.
    
    Args:
        history: List of message dictionaries containing 'role', 'content', etc.
        provider: Provider identifier String (e.g. 'openai', 'anthropic', 'google', 'open_router').
        
    Returns:
        List of LangChain message objects (HumanMessage, AIMessage, ToolMessage).
    """
    langchain_messages = []
    
    for msg in history:
        role = msg.get("role")
        content = msg.get("content", [])
        
        if role == "user":
            formatted_content = _format_content_blocks(content, provider)
            # Pass content directly to support multimodal lists or strings
            langchain_messages.append(HumanMessage(content=formatted_content))
            
        elif role == "assistant":
            tool_calls = []
            filtered_content = []
            
            if isinstance(content, str):
                filtered_content = content
            elif isinstance(content, list):
                for block in content:
                    if block.get("type") in ["tool_call", "function"]:
                        tool_calls.append({
                            "id": block.get("tool_call_id", block.get("id")),
                            "name": block.get("name"),
                            "args": block.get("input", block.get("args", {})),
                            "type": "function"
                        })
                    else:
                        filtered_content.append(block)
                
                # If there's no visible content but we have tool calls, use empty string
                if not filtered_content and tool_calls:
                    filtered_content = ""
                else:
                    filtered_content = _format_content_blocks(filtered_content, provider)
                    # If there's only one text block, unpack it to a string for cleaner simple messages
                    if isinstance(filtered_content, list) and len(filtered_content) == 1 and filtered_content[0].get("type") == "text":
                        filtered_content = filtered_content[0].get("text", "")
            
            langchain_messages.append(AIMessage(content=filtered_content, tool_calls=tool_calls))
            
        elif role == "tool":
            tool_call_id = msg.get("tool_call_id")
            # fallback for 'name' vs 'tool_name' since both have been used in custom history structures
            name = msg.get("name", msg.get("tool_name", ""))
            artifact = msg.get("artifact", None)
            
            parsed_content = _format_content_blocks(content, provider)
            if isinstance(parsed_content, list):
                # Optionally unpack single text block to string
                if len(parsed_content) == 1 and parsed_content[0].get("type") == "text":
                    parsed_content = parsed_content[0].get("text", "")
            
            tool_msg_kwargs = {
                "content": parsed_content,
                "tool_call_id": tool_call_id,
                "name": name
            }
            if artifact is not None:
                tool_msg_kwargs["artifact"] = artifact
                
            langchain_messages.append(ToolMessage(**tool_msg_kwargs))
            
    return langchain_messages
