from typing import Optional
from langchain_tavily import TavilySearch
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from datetime import datetime
from pathlib import Path
import json
import os
import re
import requests
import httpx

from app.LLM.memory import Memory
from app.helper import update_memory


async def web_search(query: str, num_results: Optional[int] = 20, memory: Optional[Memory] = None, tool_call_id: Optional[str] = None):
    """
    This is the web search tool which takes the query, max_results and returns the results.

    input:
        query: str
        num_results: Optional[int] = 20
        memory: Optional[Memory] = None
        tool_call_id: Optional[str] = None

    output:
        result: list[dict]
    """
    try:
        search_tool = TavilySearch(max_results= num_results, topic= "general")    

        result = search_tool.invoke({"query": query})
        
        # Update local memory with the conversation
        update_memory(role="user", content=query, memory=memory)
        update_memory(role="tool", name="web_search", tool_call_id=tool_call_id, content=json.dumps(result), memory=memory)
        
       # WIP - sending the websocket message to client.

        return json.dumps(result)
    except Exception as e:
        return f"Error in web search for query: {query}\n error: {e}"
    

async def web_scraper(urls_string: str, workspace_path: str, chat_name: str, memory: Optional[Memory] = None, tool_call_id: Optional[str] = None):
    """
    This is the web scraping tool which takes the url's seprated by comma, workspace_path, chat_name and saves the scraped data at the provided location.

    input:
        urls_string: str
        workspace_path: str
        chat_name: str
        memory: Optional[Memory] = None
        tool_call_id: Optional[str] = None

    output:
        result: str
    """
    def ensure_https(url):
        return url if url.startswith("http") else "https://" + url

    def sanitize_filename(text):
        return re.sub(r'[^a-zA-Z0-9_\-]', '_', text)

    def extract_clean_domain(url):
        netloc = urlparse(url).netloc
        parts = netloc.replace("www.", "").split(".")
        return parts[0] if parts else sanitize_filename(netloc)

    def create_filename(url):
        parsed = urlparse(url)
        domain = extract_clean_domain(url)
        path = parsed.path.strip("/").replace("/", "_")
        combined = f"{domain}_{path}" if path else domain
        return sanitize_filename(combined) + ".txt"

    async def fetch_content(client, url):
        try:
            response = await client.get(url, timeout=10)
            response.raise_for_status()
            return response.text
        except Exception as e:
            return str(e)  # Return the error message as string

    def extract_main_content(html):
        soup = BeautifulSoup(html, 'html.parser')
        title = soup.title.string.strip() if soup.title and soup.title.string else "Untitled"

        for tag in soup(['script', 'style', 'noscript', 'header', 'footer', 'svg', 'img']):
            tag.decompose()

        main = soup.find('main') or soup.body or soup
        text = main.get_text(separator="\n", strip=True)
        return title, text

    def save_markdown(base_dir, chat_name, url, title, content):
        file_name = create_filename(url)
        save_dir = base_dir / sanitize_filename(chat_name)
        save_dir.mkdir(parents=True, exist_ok=True)

        file_path = save_dir / file_name
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        markdown = f"# {title}\n\n"
        markdown += f"**URL:** {url}\n\n"
        markdown += f"**Scraped on:** {timestamp}\n\n"
        markdown += "---\n\n"
        markdown += content

        file_path.write_text(markdown, encoding="utf-8")
        return str(file_path)

    # Begin tool logic
    urls = [ensure_https(u.strip()) for u in urls_string.split(",") if u.strip()]
    base_dir = Path(workspace_path) / "scrapes"
    results = []

    async with httpx.AsyncClient() as client:
        for url in urls:
            html_or_error = await fetch_content(client, url)
            if isinstance(html_or_error, str) and html_or_error.startswith("<"):
                title, content = extract_main_content(html_or_error)
                file_path = save_markdown(base_dir, chat_name, url, title, content)
                results.append({
                    "url": url,
                    "success": True,
                    "file_path": file_path
                })
            else:
                results.append({
                    "url": url,
                    "success": False,
                    "error": html_or_error or "Unknown error"
                })

    successful = len([r for r in results if r["success"]])
    failed = len(results) - successful

    # Construct final message
    if successful == len(results):
        message = f"Successfully scraped all {len(results)} URLs. Results saved to:"
        for r in results:
            message += f"\n- {r.get('file_path')}"
    elif successful > 0:
        message = f"Scraped {successful} URLs successfully and {failed} failed. Results saved to:"
        for r in results:
            if r["success"]:
                message += f"\n- {r.get('file_path')}"
        message += "\n\nFailed URLs:"
        for r in results:
            if not r["success"]:
                message += f"\n- {r['url']}: {r.get('error', 'Unknown error')}"
    else:
        error_details = "; ".join([f"{r['url']}: {r.get('error', 'Unknown error')}" for r in results])
        return f"Failed to scrape all {len(results)} URLs. Errors: {error_details}"
    
    # Update local memory with the conversation
    update_memory(role="user", content=urls_string, memory=memory)
    update_memory(role="tool", name="web_scraper", tool_call_id=tool_call_id, content=message, memory=memory)

    return message