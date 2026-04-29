import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dateutil import parser as date_parser


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def stable_hash(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update((part or "").encode("utf-8"))
    return h.hexdigest()


def canonicalize_url(url: str) -> str:
    if not url:
        return ""
    raw = url.strip()
    if raw.startswith("/"):
        return ""
    if "://" not in raw and not raw.startswith("//"):
        raw = f"https://{raw}"
    parsed = urlsplit(raw)
    scheme = parsed.scheme.lower() or "https"
    host = parsed.netloc.lower()
    if not host:
        return ""
    path = parsed.path.rstrip("/") or "/"
    query = _canonical_query(parsed.query)
    return urlunsplit((scheme, host, path, query, ""))


TRACKING_QUERY_PARAMS = {
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "mkt_tok",
    "ref",
    "spm",
}


def _canonical_query(query: str) -> str:
    if not query:
        return ""
    kept = []
    for key, value in parse_qsl(query, keep_blank_values=True):
        lowered = key.lower()
        if lowered.startswith("utm_") or lowered in TRACKING_QUERY_PARAMS:
            continue
        kept.append((key, value))
    return urlencode(sorted(kept))


ARXIV_ID_RE = re.compile(
    r"(?:arxiv:|arxiv\.org/(?:abs|pdf)/|[?&]id=)?\b([a-z-]+(?:\.[a-z-]+)?/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?\b",
    re.IGNORECASE,
)


def arxiv_dedupe_key(*values: str) -> str:
    for value in values:
        if not value:
            continue
        match = ARXIV_ID_RE.search(value)
        if match:
            return f"arxiv:{match.group(1).lower()}"
    return ""


def normalize_title(value: str) -> str:
    normalized = re.sub(r"[^\w\s-]", " ", value.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def published_day(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value or "")[:10]


def dedupe_key_from_parts(canonical_url: str, title: str, published_at: Any, scope: str, *identity_values: str) -> str:
    arxiv_key = arxiv_dedupe_key(canonical_url, *identity_values)
    if arxiv_key:
        return arxiv_key
    if canonical_url:
        return f"url:{stable_hash(canonical_url)}"
    normalized_title = normalize_title(title)
    if normalized_title:
        return f"title:{scope or 'source'}:{published_day(published_at)}:{stable_hash(normalized_title)[:24]}"
    return f"entry:{scope or 'source'}:{stable_hash(*identity_values)}"


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (tuple, list)) and len(value) >= 6:
        return datetime(*value[:6], tzinfo=timezone.utc)
    try:
        dt = date_parser.parse(str(value))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def text_matches(text: str, include: list[str], exclude: list[str]) -> bool:
    lowered = text.lower()
    if include and not any(term.lower() in lowered for term in include):
        return False
    return not any(term.lower() in lowered for term in exclude)


def extract_entities(text: str) -> list[str]:
    seen: set[str] = set()
    entities: list[str] = []
    for match in re.findall(r"\b(?:[A-Z][A-Za-z0-9]+(?:[- ][A-Z0-9][A-Za-z0-9]+){0,3}|[A-Za-z]+-\d+(?:\.\d+)?)\b", text):
        if len(match) < 3 or match in seen:
            continue
        seen.add(match)
        entities.append(match)
        if len(entities) >= 12:
            break
    return entities


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
