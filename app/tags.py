import re
import unicodedata
from typing import Iterable


DEFAULT_TAGGING = {"mode": "llm", "max_tags": 5}
TAGGING_MODES = {"feed", "llm", "default"}

_STRUCTURAL_TAGS = {
    "article",
    "body",
    "button",
    "card",
    "category",
    "categories",
    "class",
    "container",
    "content",
    "feed",
    "footer",
    "grid",
    "header",
    "homepage",
    "layout",
    "main",
    "menu",
    "nav",
    "page",
    "post",
    "row",
    "rss",
    "section",
    "tag",
    "tags",
    "wrapper",
}

_CSS_CLASS_PATTERNS = [
    re.compile(r"^(?:m|p)(?:t|r|b|l|x|y)?-\d+$"),
    re.compile(r"^(?:col|cols|span|start|end|order|offset)-\d+$"),
    re.compile(r"^(?:w|h|min-w|min-h|max-w|max-h)-\d+$"),
    re.compile(r"^(?:text|bg|border|rounded|gap|space|z|top|right|bottom|left)-[\w-]+$"),
]


def normalize_tag(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    text = text.strip().lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def is_bad_tag(tag: str) -> bool:
    if not tag or len(tag) > 40 or tag.isdigit():
        return True
    if len(tag) < 2:
        return True
    if tag in _STRUCTURAL_TAGS:
        return True
    return any(pattern.match(tag) for pattern in _CSS_CLASS_PATTERNS)


def sanitize_tags(values: Iterable[object], max_tags: int | None = None) -> list[str]:
    seen: set[str] = set()
    tags: list[str] = []
    for value in values or []:
        tag = normalize_tag(value)
        if is_bad_tag(tag) or tag in seen:
            continue
        seen.add(tag)
        tags.append(tag)
        if max_tags is not None and len(tags) >= max_tags:
            break
    return tags


def merge_tags(*groups: Iterable[object], max_tags: int | None = None) -> list[str]:
    merged: list[object] = []
    for group in groups:
        merged.extend(list(group or []))
    return sanitize_tags(merged, max_tags=max_tags)


def normalize_tagging_config(value: object) -> dict[str, int | str]:
    config = value if isinstance(value, dict) else {}
    mode = str(config.get("mode") or DEFAULT_TAGGING["mode"]).strip().lower()
    if mode not in TAGGING_MODES:
        mode = str(DEFAULT_TAGGING["mode"])
    try:
        max_tags = int(config.get("max_tags") or DEFAULT_TAGGING["max_tags"])
    except (TypeError, ValueError):
        max_tags = int(DEFAULT_TAGGING["max_tags"])
    return {"mode": mode, "max_tags": max(1, min(max_tags, 12))}
