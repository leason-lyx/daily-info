import re

import httpx
from bs4 import BeautifulSoup


def strip_html(value: str) -> str:
    if not value:
        return ""
    soup = BeautifulSoup(value, "html.parser")
    return re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True)).strip()


async def extract_generic_article(url: str, timeout: int = 20) -> tuple[str, str]:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url, headers={"User-Agent": "daily-info/0.1"})
    if response.status_code >= 400:
        return "", f"GET returned {response.status_code}"
    soup = BeautifulSoup(response.text, "html.parser")
    for selector in ["article", "main", "[role=main]", ".post-content", ".entry-content"]:
        node = soup.select_one(selector)
        if node:
            text = re.sub(r"\n{3,}", "\n\n", node.get_text("\n", strip=True)).strip()
            if len(text) > 200:
                return text, ""
    body = soup.body.get_text("\n", strip=True) if soup.body else ""
    return re.sub(r"\n{3,}", "\n\n", body).strip(), ""

