from dataclasses import dataclass, field
from typing import Protocol
from typing import Any
from urllib.parse import urljoin

import feedparser
import httpx
from bs4 import BeautifulSoup

from app.config import Settings
from app.models import SourceAttempt
from app.utils import loads, parse_datetime


RSSHUB_TIMEOUT_SECONDS = 45


@dataclass
class RawEntryData:
    title: str
    url: str
    published_at: Any = None
    authors: list[str] = field(default_factory=list)
    summary: str = ""
    content: str = ""
    tags: list[str] = field(default_factory=list)
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class AdapterResult:
    entries: list[RawEntryData]
    warnings: list[str] = field(default_factory=list)
    used_url: str | None = None
    used_rsshub_instance: str | None = None


class AdapterError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class Adapter(Protocol):
    async def fetch(self, attempt: SourceAttempt, settings: Settings) -> AdapterResult:
        ...


def _entry_content(entry: Any) -> str:
    if entry.get("content"):
        return "\n\n".join(str(part.get("value", "")) for part in entry.get("content", []) if part.get("value"))
    return str(entry.get("summary") or entry.get("description") or "")


def _authors(entry: Any) -> list[str]:
    if entry.get("authors"):
        return [a.get("name", "") for a in entry.get("authors", []) if a.get("name")]
    if entry.get("author"):
        return [str(entry.get("author"))]
    return []


def _entry_tags(entry: Any) -> list[str]:
    values: list[str] = []
    for tag in entry.get("tags") or []:
        if isinstance(tag, dict):
            values.extend(str(tag.get(key) or "") for key in ["term", "label"] if tag.get(key))
        elif tag:
            values.append(str(tag))
    for key in ["category", "categories"]:
        raw_value = entry.get(key)
        if isinstance(raw_value, list):
            values.extend(str(value) for value in raw_value if value)
        elif raw_value:
            values.append(str(raw_value))
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        value = value.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


async def fetch_feed(url: str, timeout: int = 20) -> AdapterResult:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url, headers={"User-Agent": "daily-info/0.1"})
    if response.status_code >= 400:
        raise AdapterError("http_error", f"GET {url} returned {response.status_code}")
    parsed = feedparser.parse(response.content)
    warnings: list[str] = []
    if parsed.bozo:
        warnings.append(str(getattr(parsed, "bozo_exception", "feed parse warning")))
    entries: list[RawEntryData] = []
    for entry in parsed.entries:
        link = entry.get("link") or entry.get("id") or ""
        entries.append(
            RawEntryData(
                title=str(entry.get("title") or link or "Untitled"),
                url=link,
                published_at=parse_datetime(entry.get("published_parsed") or entry.get("updated_parsed") or entry.get("published")),
                authors=_authors(entry),
                summary=str(entry.get("summary") or ""),
                content=_entry_content(entry),
                tags=_entry_tags(entry),
                raw_payload=dict(entry),
            )
        )
    if not entries:
        raise AdapterError("empty_feed", f"No entries parsed from {url}")
    return AdapterResult(entries=entries, warnings=warnings, used_url=url)


async def discover_feed(url: str, timeout: int = 20) -> tuple[str | None, list[str]]:
    warnings: list[str] = []
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url, headers={"User-Agent": "daily-info/0.1"})
    if response.status_code >= 400:
        raise AdapterError("http_error", f"GET {url} returned {response.status_code}")
    parsed = feedparser.parse(response.content)
    if parsed.entries:
        return str(response.url), warnings
    soup = BeautifulSoup(response.text, "html.parser")
    for link in soup.find_all("link"):
        rel = " ".join(link.get("rel", []))
        kind = link.get("type", "")
        href = link.get("href")
        if href and ("alternate" in rel or "rss" in kind or "atom" in kind or "xml" in kind):
            return urljoin(str(response.url), href), warnings
    warnings.append("No RSS/Atom feed link discovered; HTML fallback may be incomplete.")
    return None, warnings


async def fetch_html_index(url: str, timeout: int = 20) -> AdapterResult:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url, headers={"User-Agent": "daily-info/0.1"})
    if response.status_code >= 400:
        raise AdapterError("http_error", f"GET {url} returned {response.status_code}")
    soup = BeautifulSoup(response.text, "html.parser")
    entries: list[RawEntryData] = []
    for anchor in soup.select("article a[href], main a[href], h2 a[href], h3 a[href]"):
        title = anchor.get_text(" ", strip=True)
        href = anchor.get("href")
        if not title or not href:
            continue
        url_abs = urljoin(str(response.url), href)
        if any(e.url == url_abs for e in entries):
            continue
        entries.append(RawEntryData(title=title, url=url_abs, raw_payload={"source": "html_index"}))
        if len(entries) >= 20:
            break
    if not entries:
        raise AdapterError("empty_html", f"No article links found in {url}")
    return AdapterResult(entries=entries, warnings=["HTML index fallback has lower field fidelity."], used_url=url)


class FeedAdapter:
    async def fetch(self, attempt: SourceAttempt, settings: Settings) -> AdapterResult:
        config = loads(attempt.config, {})
        timeout = int(config.get("timeout_seconds", config.get("timeout", 20)))
        return await fetch_feed(attempt.url, timeout=timeout)


class RsshubAdapter:
    async def fetch(self, attempt: SourceAttempt, settings: Settings) -> AdapterResult:
        route = attempt.route or attempt.url
        config = loads(attempt.config, {})
        timeout = int(config.get("timeout_seconds", config.get("timeout", RSSHUB_TIMEOUT_SECONDS)))
        if route.startswith("http://") or route.startswith("https://"):
            return await fetch_feed(route, timeout=timeout)
        errors: list[str] = []
        for instance in settings.rsshub_instances:
            url = f"{instance}/{route.lstrip('/')}"
            try:
                result = await fetch_feed(url, timeout=timeout)
                result.used_rsshub_instance = instance
                return result
            except AdapterError as exc:
                errors.append(f"{instance}: {exc.message}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{instance}: {type(exc).__name__} {exc}")
        raise AdapterError("rsshub_failed", "\n".join(errors) or "All RSSHub instances failed")


class HtmlIndexAdapter:
    async def fetch(self, attempt: SourceAttempt, settings: Settings) -> AdapterResult:
        config = loads(attempt.config, {})
        timeout = int(config.get("timeout_seconds", config.get("timeout", 20)))
        return await fetch_html_index(attempt.url, timeout=timeout)


ADAPTERS: dict[str, Adapter] = {
    "feed": FeedAdapter(),
    "rsshub": RsshubAdapter(),
    "html_index": HtmlIndexAdapter(),
}


async def run_attempt(attempt: SourceAttempt, settings: Settings) -> AdapterResult:
    if attempt.adapter == "manual":
        raise AdapterError("manual_not_supported", "Manual import is reserved for a later workflow.")
    adapter = ADAPTERS.get(attempt.adapter)
    if not adapter:
        raise AdapterError("unknown_adapter", f"Unsupported adapter: {attempt.adapter}")
    return await adapter.fetch(attempt, settings)


async def preview_source(url: str | None, route: str | None, adapter: str, settings: Settings, timeout_seconds: int = 20) -> AdapterResult:
    warnings: list[str] = []
    if adapter == "rsshub":
        attempt = SourceAttempt(kind="rsshub", adapter="rsshub", url=url or "", route=route or "", config=dumps({"timeout_seconds": timeout_seconds}))
        return await run_attempt(attempt, settings)
    if adapter == "html_index":
        if not url:
            raise AdapterError("missing_url", "URL is required for HTML preview")
        return await fetch_html_index(url, timeout=timeout_seconds)
    if not url:
        raise AdapterError("missing_url", "URL is required")
    feed_url, discover_warnings = await discover_feed(url)
    warnings.extend(discover_warnings)
    if feed_url:
        result = await fetch_feed(feed_url, timeout=timeout_seconds)
        result.warnings = warnings + result.warnings
        return result
    result = await fetch_html_index(url, timeout=timeout_seconds)
    result.warnings = warnings + result.warnings
    return result
