from langchain.tools import tool
from bs4 import BeautifulSoup
import httpx
from urllib.parse import urlparse
import json



@tool("web_scraping_tool")
async def web_scraping_tool(urls: str) -> dict:
    """
    Scrape readable text from one or more URLs. When we have the url of the page and had questions
    related to page use this tool.
    When we didn't got the full answer from the web search or got little context of the page and got
    assurance from the little context of the page that it could have answer what user this tool.
    Whenever required full context of the page use this tool.
    Dont use everytime if not needed because it will increase the token usage.

    Input:
        urls: Comma-separated list of URLs
              Example: "https://example.com, https://openai.com"

    Output:
        "{
            "status": "success",
            "results": [
                {
                    "url": "...",
                    "success": true,
                    "title": "...",
                    "content": "..."
                }
            ]
        }"
    """

    def ensure_https(url: str) -> str:
        return url if url.startswith("http") else f"https://{url}"

    async def fetch(client: httpx.AsyncClient, url: str):
        try:
            res = await client.get(url, timeout=10)
            res.raise_for_status()
            return res.text
        except Exception as e:
            return {"error": str(e)}

    def extract_content(html: str):
        soup = BeautifulSoup(html, "html.parser")

        title = (
            soup.title.string.strip()
            if soup.title and soup.title.string
            else "Untitled"
        )

        for tag in soup(["script", "style", "noscript", "header", "footer", "svg", "img"]):
            tag.decompose()

        main = soup.find("main") or soup.body or soup
        text = main.get_text(separator="\n", strip=True)

        return title, text

    url_list = [ensure_https(u.strip()) for u in urls.split(",") if u.strip()]
    results = []

    async with httpx.AsyncClient(follow_redirects=True) as client:
        for url in url_list:
            html = await fetch(client, url)

            if isinstance(html, dict) and "error" in html:
                results.append({
                    "url": url,
                    "success": False,
                    "error": html["error"]
                })
                continue

            title, content = extract_content(html)

            results.append({
                "url": url,
                "success": True,
                "title": title,
                "content": content
            })

    response = {
        "status": "success",
        "results": results
    }
    return json.dumps(response)