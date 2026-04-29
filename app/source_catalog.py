from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from app.models import Source, SourceAttempt
from app.schemas import FetchAttemptIn, SourceDefinitionIn
from app.utils import dumps, loads


SOURCE_CATALOG_DIR = Path(__file__).resolve().parent.parent / "config" / "sources"


def canonical_definition_json(definition: SourceDefinitionIn) -> str:
    return json.dumps(definition.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def source_definition_hash(definition: SourceDefinitionIn) -> str:
    return hashlib.sha256(canonical_definition_json(definition).encode("utf-8")).hexdigest()


def load_source_catalog(directory: str | Path = SOURCE_CATALOG_DIR) -> list[tuple[SourceDefinitionIn, str]]:
    root = Path(directory)
    definitions: list[tuple[SourceDefinitionIn, str]] = []
    seen: dict[str, str] = {}
    for path in sorted(root.glob("*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if payload.get("schema_version") != 1:
            raise ValueError(f"{path} must declare schema_version: 1")
        for raw in payload.get("sources", []):
            definition = SourceDefinitionIn.model_validate(raw)
            if definition.id in seen:
                raise ValueError(f"Duplicate source id {definition.id!r} in {path} and {seen[definition.id]}")
            seen[definition.id] = path.name
            definitions.append((definition, path.name))
    return definitions


def sync_source_catalog(db: Session, directory: str | Path = SOURCE_CATALOG_DIR) -> int:
    count = 0
    for definition, catalog_file in load_source_catalog(directory):
        upsert_source_definition(db, definition, catalog_file=catalog_file, builtin=True)
        count += 1
    db.commit()
    return count


def upsert_source_definition(db: Session, definition: SourceDefinitionIn, catalog_file: str = "custom", builtin: bool = False) -> Source:
    source = db.get(Source, definition.id)
    if source and not source.is_builtin and builtin:
        return source
    if not source:
        source = Source(id=definition.id, name=definition.title, content_type=definition.kind)
        db.add(source)
    apply_definition(source, definition, catalog_file=catalog_file, builtin=builtin)
    source.attempts.clear()
    source.attempts = [attempt_model(attempt, index) for index, attempt in enumerate(definition.fetch.attempts)]
    return source


def apply_definition(source: Source, definition: SourceDefinitionIn, catalog_file: str, builtin: bool) -> None:
    summary = definition.summary
    auth = definition.auth
    filters = definition.filters
    spec_json = canonical_definition_json(definition)
    source.name = definition.title
    source.content_type = definition.kind
    source.platform = definition.platform
    source.homepage_url = definition.homepage
    source.is_builtin = builtin
    source.group = definition.group
    source.priority = definition.priority
    source.poll_interval = definition.fetch.interval_seconds
    source.auto_summary_enabled = bool(summary.auto if summary else False)
    source.auto_summary_days = int(summary.window_days if summary else 7)
    source.language_hint = definition.language
    source.include_keywords = dumps(filters.include_keywords)
    source.exclude_keywords = dumps(filters.exclude_keywords)
    source.default_tags = dumps(definition.tags)
    source.fetch = dumps(definition.fetch.model_dump(mode="json"))
    source.fulltext = dumps(definition.fulltext.model_dump(mode="json"))
    source.summary = dumps(summary.model_dump(mode="json") if summary else {})
    source.auth = dumps(auth.model_dump(mode="json"))
    source.auth_mode = auth.mode
    source.stability_level = definition.stability
    source.spec_json = spec_json
    source.spec_hash = hashlib.sha256(spec_json.encode("utf-8")).hexdigest()
    source.catalog_file = catalog_file
    # Legacy column kept for compatibility only; subscription is the new truth.
    source.enabled = False


def attempt_model(data: FetchAttemptIn, index: int) -> SourceAttempt:
    config: dict[str, Any] = {
        "timeout_seconds": data.timeout_seconds,
        "selectors": data.selectors,
        "limit": data.limit,
    }
    return SourceAttempt(
        kind="rsshub" if data.adapter == "rsshub" else "direct",
        adapter=data.adapter,
        url=data.url,
        route=data.route,
        priority=index,
        enabled=True,
        config=dumps({key: value for key, value in config.items() if value not in ("", [], None)}),
    )


def definition_from_source(source: Source) -> SourceDefinitionIn:
    spec = loads(source.spec_json, None)
    if isinstance(spec, dict) and spec:
        return SourceDefinitionIn.model_validate(spec)
    return SourceDefinitionIn(
        id=source.id,
        title=source.name,
        kind=source.content_type,
        platform=source.platform,
        homepage=source.homepage_url,
        language=source.language_hint,
        tags=loads(source.default_tags, []),
        group=source.group,
        priority=source.priority,
        fetch={
            "strategy": "first_success",
            "interval_seconds": source.poll_interval,
            "attempts": [
                {
                    "adapter": attempt.adapter,
                    "url": attempt.url,
                    "route": attempt.route,
                    "timeout_seconds": int(loads(attempt.config, {}).get("timeout_seconds") or loads(attempt.config, {}).get("timeout") or 20),
                }
                for attempt in source.attempts
                if attempt.enabled
            ],
        },
        fulltext=_normalize_fulltext(loads(source.fulltext, {})),
        summary={"auto": source.auto_summary_enabled, "window_days": source.auto_summary_days},
        filters={"include_keywords": loads(source.include_keywords, []), "exclude_keywords": loads(source.exclude_keywords, [])},
        auth={"mode": source.auth_mode},
        stability=source.stability_level,
    )


def _normalize_fulltext(config: dict[str, Any]) -> dict[str, Any]:
    if "mode" in config:
        return config
    strategy = config.get("strategy", "feed_field")
    mode = {
        "feed_field": "feed_only",
        "generic_article": "detail_only",
        "feed_or_detail": "feed_then_detail",
    }.get(strategy, "feed_only")
    return {
        "mode": mode,
        "min_feed_chars": config.get("min_feed_fulltext_chars", 1200),
        "max_detail_pages_per_run": config.get("max_fulltext_per_run", 20),
    }
