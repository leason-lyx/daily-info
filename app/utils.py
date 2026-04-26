import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

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
    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.lower() or "https"
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((scheme, host, path, parsed.query, ""))


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

