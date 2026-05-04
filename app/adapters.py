from dataclasses import dataclass, field
import re
from typing import Protocol
from typing import Any
from urllib.parse import urljoin, urlparse

import feedparser
import httpx
from bs4 import BeautifulSoup

from app.config import Settings
from app.models import SourceAttempt, SourceRuntime
from app.utils import dumps, loads, parse_datetime


RSSHUB_TIMEOUT_SECONDS = 45
X_USER_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
MONTH_DATE_RE = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},\s+\d{4}\b",
    re.IGNORECASE,
)
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\((https?://[^)]+)\)")
INDEX_LABELS = {
    "announcements",
    "company",
    "economic research",
    "engineering",
    "featured",
    "global affairs",
    "ai adoption",
    "interpretability",
    "milestone",
    "policy",
    "product",
    "publication",
    "release",
    "research",
    "safety",
    "science",
    "security",
    "societal impacts",
}


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
    cursor: str | None = None


class AdapterError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class Adapter(Protocol):
    async def fetch(self, attempt: SourceAttempt, settings: Settings, runtime: SourceRuntime | None = None) -> AdapterResult:
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


def _reader_url(url: str) -> str:
    return f"https://r.jina.ai/http://r.jina.ai/http://{url}"


def _published_date(text: str) -> Any:
    match = MONTH_DATE_RE.search(text)
    return parse_datetime(match.group(0)) if match else None


def _clean_index_title(text: str) -> str:
    text = " ".join(text.split())
    date_match = MONTH_DATE_RE.search(text)
    if not date_match:
        return _drop_leading_labels(text)

    if date_match.start() <= 8:
        title = text[date_match.end() :].strip()
    else:
        title = text[: date_match.start()].strip()
    title = re.sub(r"\b\d+\s+min\s+read\b.*$", "", title, flags=re.IGNORECASE).strip()
    title = _drop_leading_labels(_drop_trailing_labels(title))
    return title or text


def _drop_leading_labels(text: str) -> str:
    current = text.strip(" -:|")
    for label in sorted(INDEX_LABELS, key=len, reverse=True):
        if current.lower() == label:
            return ""
        prefix = f"{label} "
        if current.lower().startswith(prefix):
            return current[len(prefix) :].strip(" -:|")
    return current


def _drop_trailing_labels(text: str) -> str:
    current = text.strip(" -:|")
    lowered = current.lower()
    for label in sorted(INDEX_LABELS, key=len, reverse=True):
        suffix = f" {label}"
        if lowered == label:
            return ""
        if lowered.endswith(suffix):
            return current[: -len(suffix)].strip(" -:|")
    return current


def _is_article_url(url: str) -> bool:
    if any(segment in url for segment in ["/research/index", "/research/team/", "/careers/", "/safety/"]):
        return False
    return any(segment in url for segment in ["/index/", "/news/", "/research/", "/engineering/", "/features/", "/glasswing", "/81k-interviews"])


def _entries_from_html_index(body: str, base_url: str, limit: int) -> list[RawEntryData]:
    soup = BeautifulSoup(body, "html.parser")
    entries: list[RawEntryData] = []
    seen: set[str] = set()
    for anchor in soup.select("main a[href], article a[href]"):
        text = anchor.get_text(" ", strip=True)
        href = anchor.get("href")
        if not text or not href:
            continue
        url_abs = urljoin(base_url, href)
        if url_abs in seen or not _is_article_url(url_abs):
            continue
        title = _clean_index_title(text)
        if not title or len(title) < 8:
            continue
        seen.add(url_abs)
        entries.append(
            RawEntryData(
                title=title,
                url=url_abs,
                published_at=_published_date(text),
                summary="",
                content="",
                raw_payload={"source": "page_index", "index_text": " ".join(text.split())},
            )
        )
        if len(entries) >= limit:
            break
    return entries


def _entries_from_markdown_index(body: str, base_url: str, limit: int) -> list[RawEntryData]:
    entries: list[RawEntryData] = []
    seen: set[str] = set()
    for match in MARKDOWN_LINK_RE.finditer(body):
        text = match.group(1)
        url_abs = urljoin(base_url, match.group(2))
        if url_abs in seen or not _is_article_url(url_abs):
            continue
        title = _clean_index_title(text)
        if not title or len(title) < 8:
            continue
        seen.add(url_abs)
        entries.append(
            RawEntryData(
                title=title,
                url=url_abs,
                published_at=_published_date(text),
                raw_payload={"source": "page_index_reader", "index_text": " ".join(text.split())},
            )
        )
        if len(entries) >= limit:
            break
    return entries


async def _fill_missing_page_dates(entries: list[RawEntryData], client: httpx.AsyncClient, timeout_limit: int = 5) -> None:
    checked = 0
    for entry in entries:
        if entry.published_at or checked >= timeout_limit:
            continue
        checked += 1
        try:
            response = await client.get(entry.url, headers={"User-Agent": "daily-info/0.1"})
        except httpx.HTTPError:
            continue
        if response.status_code >= 400:
            continue
        soup = BeautifulSoup(response.text, "html.parser")
        title = soup.find("h1")
        if title:
            cleaned_title = title.get_text(" ", strip=True)
            if cleaned_title:
                entry.title = cleaned_title
        text = soup.get_text(" ", strip=True)
        published_match = re.search(r"\bPublished\s+(" + MONTH_DATE_RE.pattern[2:-2] + r")", text, flags=re.IGNORECASE)
        if published_match:
            entry.published_at = parse_datetime(published_match.group(1))
        elif not entry.published_at:
            entry.published_at = _published_date(text[:800])


async def fetch_page_index(url: str, timeout: int = 20, limit: int = 20, reader_fallback: bool = False) -> AdapterResult:
    warnings: list[str] = []
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url, headers={"User-Agent": "daily-info/0.1"})
        if response.status_code >= 400 and reader_fallback:
            reader = _reader_url(url)
            response = await client.get(reader, headers={"User-Agent": "daily-info/0.1"})
            if response.status_code >= 400:
                raise AdapterError("http_error", f"GET {reader} returned {response.status_code}")
            entries = _entries_from_markdown_index(response.text, url, limit)
            warnings.append("Used reader fallback for blocked index page.")
            used_url = reader
        else:
            if response.status_code >= 400:
                raise AdapterError("http_error", f"GET {url} returned {response.status_code}")
            entries = _entries_from_html_index(response.text, str(response.url), limit)
            used_url = str(response.url)
        await _fill_missing_page_dates(entries, client)
    if not entries:
        raise AdapterError("empty_page_index", f"No article links found in {url}")
    entries.sort(key=lambda entry: entry.published_at or parse_datetime("1970-01-01"), reverse=True)
    return AdapterResult(entries=entries[:limit], warnings=warnings, used_url=used_url)


class FeedAdapter:
    async def fetch(self, attempt: SourceAttempt, settings: Settings, runtime: SourceRuntime | None = None) -> AdapterResult:
        config = loads(attempt.config, {})
        timeout = int(config.get("timeout_seconds", config.get("timeout", 20)))
        return await fetch_feed(attempt.url, timeout=timeout)


class RsshubAdapter:
    async def fetch(self, attempt: SourceAttempt, settings: Settings, runtime: SourceRuntime | None = None) -> AdapterResult:
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
    async def fetch(self, attempt: SourceAttempt, settings: Settings, runtime: SourceRuntime | None = None) -> AdapterResult:
        config = loads(attempt.config, {})
        timeout = int(config.get("timeout_seconds", config.get("timeout", 20)))
        return await fetch_html_index(attempt.url, timeout=timeout)


class PageIndexAdapter:
    async def fetch(self, attempt: SourceAttempt, settings: Settings, runtime: SourceRuntime | None = None) -> AdapterResult:
        config = loads(attempt.config, {})
        timeout = int(config.get("timeout_seconds", config.get("timeout", 20)))
        limit = int(config.get("limit", 20))
        reader_fallback = bool(config.get("reader_fallback", False))
        return await fetch_page_index(attempt.url, timeout=timeout, limit=limit, reader_fallback=reader_fallback)


class XUserAdapter:
    async def fetch(self, attempt: SourceAttempt, settings: Settings, runtime: SourceRuntime | None = None) -> AdapterResult:
        token = settings.x_bearer_token
        if not token:
            raise AdapterError("missing_x_bearer_token", "Set X_BEARER_TOKEN in .env to fetch X user posts.")

        config = loads(attempt.config, {})
        timeout = int(config.get("timeout_seconds", config.get("timeout", 20)))
        username = _normalize_x_username(attempt.route or attempt.url)
        max_results = max(5, min(100, int(config.get("max_results", config.get("limit", 20)))))
        exclude = _normalize_x_exclude(config.get("exclude", ["retweets", "replies"]))
        since_id = _x_since_id(runtime, config)
        api_base = str(config.get("api_base_url") or settings.x_api_base_url).rstrip("/")

        headers = {"Authorization": f"Bearer {token}", "User-Agent": "daily-info/0.1"}
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            user = await _x_get_json(client, f"{api_base}/users/by/username/{username}", headers, params={"user.fields": "id,name,username"})
            user_data = user.get("data") or {}
            user_id = str(user_data.get("id") or "")
            if not user_id:
                raise AdapterError("x_user_not_found", f"X user {username!r} was not found.")

            params: dict[str, str | int] = {
                "max_results": max_results,
                "tweet.fields": "id,text,created_at,author_id,conversation_id,referenced_tweets,entities,public_metrics,lang",
                "user.fields": "id,name,username",
                "expansions": "author_id",
            }
            if exclude:
                params["exclude"] = ",".join(exclude)
            if since_id:
                params["since_id"] = since_id
            payload = await _x_get_json(client, f"{api_base}/users/{user_id}/tweets", headers, params=params)

        tweets = payload.get("data") or []
        if not isinstance(tweets, list):
            raise AdapterError("x_bad_response", "X API returned an unexpected posts payload.")
        includes = payload.get("includes") or {}
        users = {str(item.get("id")): item for item in includes.get("users") or [] if isinstance(item, dict)}
        author = users.get(user_id, user_data)
        entries = [_raw_entry_from_x_tweet(tweet, author, username) for tweet in tweets if isinstance(tweet, dict)]
        cursor = str((payload.get("meta") or {}).get("newest_id") or _latest_x_id(tweets) or "")
        return AdapterResult(entries=entries, used_url=f"https://x.com/{username}", cursor=cursor or None)


def _normalize_x_username(value: str) -> str:
    value = value.strip().lstrip("@")
    if value.startswith("http://") or value.startswith("https://"):
        parsed = urlparse(value)
        value = parsed.path.strip("/").split("/", 1)[0]
    if not X_USER_RE.fullmatch(value):
        raise AdapterError("invalid_x_username", f"Invalid X username: {value!r}")
    return value


def _normalize_x_exclude(value: Any) -> list[str]:
    if isinstance(value, str):
        values = [part.strip() for part in value.split(",")]
    elif isinstance(value, list):
        values = [str(part).strip() for part in value]
    else:
        values = []
    allowed = {"retweets", "replies"}
    return [part for part in values if part in allowed]


def _x_since_id(runtime: SourceRuntime | None, config: dict[str, Any]) -> str:
    configured = str(config.get("since_id") or "").strip()
    if configured:
        return configured
    return str(getattr(runtime, "cursor", "") or "").strip()


async def _x_get_json(client: httpx.AsyncClient, url: str, headers: dict[str, str], params: dict[str, Any]) -> dict[str, Any]:
    response = await client.get(url, headers=headers, params=params)
    if response.status_code == 429:
        raise AdapterError("x_rate_limited", "X API rate limit exceeded.")
    if response.status_code >= 400:
        raise AdapterError("x_api_error", f"GET {url} returned {response.status_code}: {_x_error_message(response)}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise AdapterError("x_bad_response", "X API returned a non-object response.")
    return payload


def _x_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:500]
    detail = payload.get("detail") or payload.get("title") or payload.get("errors")
    return str(detail)[:500]


def _raw_entry_from_x_tweet(tweet: dict[str, Any], author: dict[str, Any], fallback_username: str) -> RawEntryData:
    tweet_id = str(tweet.get("id") or "")
    text = str(tweet.get("text") or "").strip()
    username = str(author.get("username") or fallback_username)
    title = _x_post_title(text, tweet_id)
    return RawEntryData(
        title=title,
        url=f"https://x.com/{username}/status/{tweet_id}",
        published_at=parse_datetime(tweet.get("created_at")),
        authors=[str(author.get("name") or username)],
        summary=text,
        content=text,
        raw_payload={"source": "x_user", "tweet": tweet, "author": author},
    )


def _x_post_title(text: str, tweet_id: str) -> str:
    title = " ".join(text.split())
    if len(title) > 140:
        title = f"{title[:137].rstrip()}..."
    return title or f"X post {tweet_id}"


def _latest_x_id(tweets: list[Any]) -> str:
    ids = [str(tweet.get("id")) for tweet in tweets if isinstance(tweet, dict) and str(tweet.get("id") or "").isdigit()]
    return max(ids, key=int) if ids else ""


ADAPTERS: dict[str, Adapter] = {
    "feed": FeedAdapter(),
    "rsshub": RsshubAdapter(),
    "html_index": HtmlIndexAdapter(),
    "page_index": PageIndexAdapter(),
    "x_user": XUserAdapter(),
}


async def run_attempt(attempt: SourceAttempt, settings: Settings, runtime: SourceRuntime | None = None) -> AdapterResult:
    if attempt.adapter == "manual":
        raise AdapterError("manual_not_supported", "Manual import is reserved for a later workflow.")
    adapter = ADAPTERS.get(attempt.adapter)
    if not adapter:
        raise AdapterError("unknown_adapter", f"Unsupported adapter: {attempt.adapter}")
    return await adapter.fetch(attempt, settings, runtime=runtime)


async def preview_source(url: str | None, route: str | None, adapter: str, settings: Settings, timeout_seconds: int = 20) -> AdapterResult:
    warnings: list[str] = []
    if adapter == "rsshub":
        attempt = SourceAttempt(kind="rsshub", adapter="rsshub", url=url or "", route=route or "", config=dumps({"timeout_seconds": timeout_seconds}))
        return await run_attempt(attempt, settings)
    if adapter == "html_index":
        if not url:
            raise AdapterError("missing_url", "URL is required for HTML preview")
        return await fetch_html_index(url, timeout=timeout_seconds)
    if adapter == "page_index":
        if not url:
            raise AdapterError("missing_url", "URL is required for page index preview")
        return await fetch_page_index(url, timeout=timeout_seconds, reader_fallback=True)
    if adapter == "x_user":
        attempt = SourceAttempt(adapter="x_user", route=route or url or "", config=dumps({"timeout_seconds": timeout_seconds}))
        return await run_attempt(attempt, settings)
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
