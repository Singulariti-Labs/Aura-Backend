import httpx
import re
from bs4 import BeautifulSoup
from urllib.parse import urlparse


async def simple_web_scraper(urls_string: str) -> dict:
    """
    Scrapes readable text from one or multiple URLs (comma-separated)
    and returns the extracted content in a structured dictionary.

    Input:
        urls_string: "url1, url2, url3"

    Output:
        {
            "status": "success",
            "results": [
                {
                    "url": "...",
                    "success": True/False,
                    "title": "...",
                    "content": "...",
                    "error": "..." (only if failed)
                }
            ]
        }
    """

    def ensure_https(url: str) -> str:
        return url if url.startswith("http") else "https://" + url

    async def fetch(client, url: str):
        try:
            response = await client.get(url, timeout=10)
            response.raise_for_status()
            return response.text
        except Exception as e:
            return {"error": str(e)}

    def extract_content(html: str):
        soup = BeautifulSoup(html, "html.parser")

        # Extract title safely
        title = soup.title.string.strip() if soup.title and soup.title.string else "Untitled"

        # Remove non-text elements
        for tag in soup(["script", "style", "noscript", "header", "footer", "svg", "img"]):
            tag.decompose()

        main = soup.find("main") or soup.body or soup
        text = main.get_text(separator="\n", strip=True)

        return title, text

    urls = [ensure_https(u.strip()) for u in urls_string.split(",") if u.strip()]
    results = []

    async with httpx.AsyncClient() as client:
        for url in urls:
            html = await fetch(client, url)

            if isinstance(html, dict) and "error" in html:
                # Error
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

    return {
        "status": "success",
        "results": results
    }
