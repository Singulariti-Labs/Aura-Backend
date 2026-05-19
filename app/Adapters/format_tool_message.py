from typing import List, Union, Optional, Dict, Any, Literal, TypedDict


LLMProvider = Literal["anthropic", "openai", "gemini"]

class FileAttachment(TypedDict, total=False):
    name: str
    path: str
    type: str  # MIME type — e.g. "application/pdf", "text/csv", "text/docx"
    content: str  # Plain text content for non-PDF files; base64 for PDF/images

class ImageAttachment(TypedDict, total=False):
    name: str
    path: str
    content: str  # Always base64-encoded PNG/JPEG/WEBP
    mime: str  # Defaults to "image/png" if not provided

class CreateToolResponseOptions(TypedDict, total=False):
    provider: LLMProvider
    text: Optional[Union[str, List[str]]]
    files: Optional[List[FileAttachment]]
    images: Optional[List[ImageAttachment]]


# ─── Main Entry ───────────────────────────────────────────────────────────────

def create_tool_response(options: CreateToolResponseOptions) -> List[Dict[str, Any]]:
    """
    Main entry point to format tool responses for different LLM providers.
    """
    provider = options.get("provider", "anthropic").lower()
    text = options.get("text")
    files = options.get("files", [])
    images = options.get("images", [])

    # Normalise text into array
    text_blocks: List[str] = []
    if text:
        if isinstance(text, list):
            text_blocks = [t for t in text if t]
        else:
            text_blocks = [text]

    if provider == "anthropic":
        return _format_for_anthropic(text_blocks, files, images)
    elif provider == "openai":
        return _format_for_openai(text_blocks, files, images)
    elif provider == "gemini":
        return _format_for_gemini(text_blocks, files, images)
    else:
        return _format_for_anthropic(text_blocks, files, images)


# ─── Anthropic ────────────────────────────────────────────────────────────────

def _format_for_anthropic(
    text_blocks: List[str],
    files: List[FileAttachment],
    images: List[ImageAttachment],
) -> List[Dict[str, Any]]:
    content = []

    # Images — vision blocks
    for img in images:
        if not img.get("content"):
            continue
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": img.get("mime") or "image/png",
                "data": img["content"],
            },
        })

    # PDFs — document blocks
    for file in files:
        if not file.get("content"):
            continue
        if _is_pdf(file.get("type", "")):
            content.append({
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": file["content"],
                },
            })

    # Non-PDF files — concatenated into a single text block
    file_text = _build_file_text(files)
    if file_text:
        content.append({"type": "text", "text": file_text})

    # User text blocks
    for text in text_blocks:
        content.append({"type": "text", "text": text})

    return content


# ─── OpenAI ───────────────────────────────────────────────────────────────────

def _format_for_openai(
    text_blocks: List[str],
    files: List[FileAttachment],
    images: List[ImageAttachment],
) -> List[Dict[str, Any]]:
    content = []

    # Images — image_url blocks
    for img in images:
        if not img.get("content"):
            continue
        mime = img.get("mime") or "image/png"
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{img['content']}"},
        })

    # PDFs — input_file blocks
    for file in files:
        if not file.get("content"):
            continue
        if _is_pdf(file.get("type", "")):
            content.append({
                "type": "input_file",
                "filename": file.get("name") or "file.pdf",
                "file_data": f"data:application/pdf;base64,{file['content']}",
            })

    # Non-PDF files — concatenated text block
    file_text = _build_file_text(files)
    if file_text:
        content.append({"type": "text", "text": file_text})

    # User text blocks
    for text in text_blocks:
        content.append({"type": "text", "text": text})

    return content


# ─── Gemini ───────────────────────────────────────────────────────────────────

def _format_for_gemini(
    text_blocks: List[str],
    files: List[FileAttachment],
    images: List[ImageAttachment],
) -> List[Dict[str, Any]]:
    parts = []

    # 1. Add tool functionResponse part
    result_text = "Screenshot captured successfully."
    if text_blocks:
        result_text = "\n".join(text_blocks)
        
    parts.append({
        "functionResponse": {
            "name": "screenshot",
            "response": { "result": result_text }
        }
    })

    # 2. Add inlineData part for each image
    for img in images:
        if not img.get("content"):
            continue
        parts.append({
            "inlineData": {
                "mimeType": img.get("mime") or "image/png",
                "data": img["content"]
            }
        })

    # 3. Add inlineData part for each PDF file
    for file in files:
        if not file.get("content"):
            continue
        if _is_pdf(file.get("type", "")):
            parts.append({
                "inlineData": {
                    "mimeType": "application/pdf",
                    "data": file["content"]
                }
            })

    return parts


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _is_pdf(mime_or_ext: str) -> bool:
    return "pdf" in mime_or_ext.lower()


def _build_file_text(files: List[FileAttachment]) -> str:
    """Concatenate all non-PDF file contents into a labelled text block"""
    parts = []
    for file in files:
        if not file.get("content") or _is_pdf(file.get("type", "")):
            continue
        
        labels = []
        if file.get("name"):
            labels.append(f"name: {file['name']}")
        if file.get("path"):
            labels.append(f"path: {file['path']}")
            
        label_str = ", ".join(labels)
        parts.append(f"---- {label_str}\n{file['content']}")
        
    return "\n\n".join(parts)