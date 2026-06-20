from typing import List, Any, Dict


def prepareMessageForAI(llm_provider: str, attached_files: List[Dict[str, Any]], attached_images: List[Dict[str, Any]], query: str, screenshot: Any = None):
    """
    Prepares the message content for different AI providers (Anthropic, Gemini, OpenAI).
    
    Args:
        llm_provider: "anthropic" | "openai" | "gemini"
        attached_files: List of file objects (PDF is base64, others are text/csv/xlsx/pptx)
        attached_images: List of image objects (all base64)
        query: User query string
        screenshot: Optional base64 screenshot or list of screenshots
        
    Returns:
        Formatted content block(s) for the LLM.
    """
    
    # 1. Process all text-based files into a single string
    text_files_content = ""
    if attached_files:
        for file in attached_files:
            file_type = file.get('type', '').lower()
            if 'pdf' not in file_type:
                text_files_content += f"\n----name: {file.get('name')},\n----path: {file.get('path')}\n\n{file.get('content')}\n"
    
    llm_provider = llm_provider.lower()
    
    if "openai" in llm_provider:
        return _format_for_openai(text_files_content, attached_files, attached_images, query, screenshot)
    elif "anthropic" in llm_provider:
        return _format_for_anthropic(text_files_content, attached_files, attached_images, query, screenshot)
    elif "gemini" in llm_provider or "google" in llm_provider:
        return _format_for_gemini(text_files_content, attached_files, attached_images, query, screenshot)
    else:
        return _format_for_openai(text_files_content, attached_files, attached_images, query, screenshot)

def _format_for_openai(files_text: str, files: List[Dict[str, Any]], images: List[Dict[str, Any]], query: str, screenshot: Any = None):
    content = []
    
    # Add screenshot if provided
    if screenshot:
        if isinstance(screenshot, dict):
            screenshots_list = [screenshot.get("data") or screenshot.get("content") or screenshot.get("image_base64")]
        elif isinstance(screenshot, str):
            screenshots_list = [screenshot]
        else:
            screenshots_list = screenshot

        for shot in screenshots_list:
            if isinstance(shot, dict):
                shot_content = shot.get("data") or shot.get("content") or shot.get("image_base64")
            else:
                shot_content = shot
            if shot_content:
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{shot_content}"
                    }
                })
        content.append({
            "type": "text",
            "text": "This is the current state of the user's screen at the time of the request. Use it as context to better understand what the user is working on or referring to, especially if the query references 'this', 'that', 'here', 'there', or anything that implies something visible on screen."
        })

    # Add images
    if images:
        for img in images:
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{img.get('content')}"
                }
            })
            
    # Add PDFs
    if files:
        for file in files:
            if 'pdf' in file.get('type', '').lower():
                content.append({
                    "type": "input_file",
                    "filename": file.get('name', 'file.pdf'),
                    "file_data": f"data:application/pdf;base64,{file.get('content')}"
                })
                
    # Add text blocks separately
    if files_text:
        content.append({"type": "text", "text": files_text.strip()})
    
    content.append({"type": "text", "text": f"query: {query}"})
    return content

def _format_for_anthropic(files_text: str, files: List[Dict[str, Any]], images: List[Dict[str, Any]], query: str, screenshot: Any = None):
    content = []
    
    # Add screenshot if provided
    if screenshot:
        if isinstance(screenshot, dict):
            screenshots_list = [screenshot.get("data") or screenshot.get("content") or screenshot.get("image_base64")]
        elif isinstance(screenshot, str):
            screenshots_list = [screenshot]
        else:
            screenshots_list = screenshot

        for shot in screenshots_list:
            if isinstance(shot, dict):
                shot_content = shot.get("data") or shot.get("content") or shot.get("image_base64")
            else:
                shot_content = shot
            if shot_content:
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": shot_content
                    }
                })
        content.append({
            "type": "text",
            "text": "This is the current state of the user's screen at the time of the request. Use it as context to better understand what the user is working on or referring to, especially if the query references 'this', 'that', 'here', 'there', or anything that implies something visible on screen."
        })

    # Add images
    if images:
        for img in images:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": img.get('content')
                }
            })
            
    # Add PDFs
    if files:
        for file in files:
            if 'pdf' in file.get('type', '').lower():
                content.append({
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": file.get('content')
                    }
                })
                
    # Add text blocks separately
    if files_text:
        content.append({"type": "text", "text": files_text.strip()})
        
    content.append({"type": "text", "text": f"query: {query}"})
    return content

def _format_for_gemini(files_text: str, files: List[Dict[str, Any]], images: List[Dict[str, Any]], query: str, screenshot: Any = None):
    content = []
    
    # Add screenshot if provided
    if screenshot:
        if isinstance(screenshot, dict):
            screenshots_list = [screenshot.get("data") or screenshot.get("content") or screenshot.get("image_base64")]
        elif isinstance(screenshot, str):
            screenshots_list = [screenshot]
        else:
            screenshots_list = screenshot

        for shot in screenshots_list:
            if isinstance(shot, dict):
                shot_content = shot.get("data") or shot.get("content") or shot.get("image_base64")
            else:
                shot_content = shot
            if shot_content:
                content.append({
                    "type": "media",
                    "mime_type": "image/png",
                    "data": shot_content
                })
        content.append({
            "type": "text",
            "text": "This is the current state of the user's screen at the time of the request. Use it as context to better understand what the user is working on or referring to, especially if the query references 'this', 'that', 'here', 'there', or anything that implies something visible on screen."
        })

    # Add images
    if images:
        for img in images:
            content.append({
                "type": "media",
                "mime_type": "image/png",
                "data": img.get('content')
            })
            
    # Add PDFs
    if files:
        for file in files:
            if 'pdf' in file.get('type', '').lower():
                content.append({
                    "type": "media",
                    "mime_type": "application/pdf",
                    "data": file.get('content')
                })
                
    # Add text blocks separately
    if files_text:
        content.append({"type": "text", "text": files_text.strip()})
        
    content.append({"type": "text", "text": f"query: {query}"})
    return content
