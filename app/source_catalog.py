from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from app.models import Source, SourceAttempt
from app.schemas import FetchAttemptIn, SourceDefinitionIn, SourceDefinitionPatch
from app.tags import DEFAULT_TAGGING, normalize_tagging_config
from app.utils import dumps, loads


SOURCE_CATALOG_DIR = Path(__file__).resolve().parent.parent / "config" / "sources"
CUSTOM_CATALOG_FILE = "custom.yaml"


def canonical_definition_json(definition: SourceDefinitionIn) -> str:
    return json.dumps(definition.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def source_definition_hash(definition: SourceDefinitionIn) -> str:
    return hashlib.sha256(canonical_definition_json(definition).encode("utf-8")).hexdigest()


def load_source_catalog(directory: str | Path | None = None) -> list[tuple[SourceDefinitionIn, str]]:
    root = Path(directory or SOURCE_CATALOG_DIR)
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


def append_source_definition_to_catalog(
    definition: SourceDefinitionIn,
    catalog_file: str = CUSTOM_CATALOG_FILE,
    directory: str | Path | None = None,
) -> str:
    root = Path(directory or SOURCE_CATALOG_DIR)
    if _source_id_exists(definition.id, root):
        raise ValueError("Source id already exists")
    path, payload = _catalog_payload(catalog_file, root)
    sources = _payload_sources(payload)
    sources.append(definition.model_dump(mode="json"))
    _write_catalog_payload(path, payload)
    return path.name


def update_source_definition_in_catalog(
    source_id: str,
    patch: SourceDefinitionPatch,
    *,
    catalog_file: str = "",
    current_definition: SourceDefinitionIn | None = None,
    directory: str | Path | None = None,
) -> tuple[SourceDefinitionIn, str]:
    root = Path(directory or SOURCE_CATALOG_DIR)
    found = _find_source_payload(source_id, root, preferred_file=catalog_file)
    if found is None:
        if current_definition is None:
            raise KeyError(source_id)
        catalog_file = append_source_definition_to_catalog(current_definition, directory=root)
        found = _find_source_payload(source_id, root, preferred_file=catalog_file)
    if found is None:
        raise KeyError(source_id)

    path, payload, index, raw_definition = found
    updated = apply_source_definition_patch(SourceDefinitionIn.model_validate(raw_definition), patch)
    _payload_sources(payload)[index] = updated.model_dump(mode="json")
    _write_catalog_payload(path, payload)
    return updated, path.name


def apply_source_definition_patch(definition: SourceDefinitionIn, patch: SourceDefinitionPatch) -> SourceDefinitionIn:
    data = definition.model_dump(mode="json")
    patch_data = patch.model_dump(mode="json", exclude_unset=True)
    for field in ["language", "tags", "group", "priority", "fulltext", "summary", "tagging", "filters"]:
        if field in patch_data:
            data[field] = patch_data[field]
    if "fetch" in patch_data:
        fetch_patch = patch_data["fetch"] or {}
        if "interval_seconds" in fetch_patch:
            data.setdefault("fetch", {})["interval_seconds"] = fetch_patch["interval_seconds"]
    return SourceDefinitionIn.model_validate(data)


def _source_id_exists(source_id: str, root: Path) -> bool:
    if not root.exists():
        return False
    for definition, _catalog_file in load_source_catalog(root):
        if definition.id == source_id:
            return True
    return False


def _catalog_payload(catalog_file: str, root: Path) -> tuple[Path, dict[str, Any]]:
    path = _catalog_path(catalog_file, root)
    if not path.exists():
        return path, {"schema_version": 1, "sources": []}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    if payload.get("schema_version") != 1:
        raise ValueError(f"{path} must declare schema_version: 1")
    _payload_sources(payload)
    return path, payload


def _catalog_path(catalog_file: str, root: Path) -> Path:
    filename = Path(catalog_file or CUSTOM_CATALOG_FILE).name
    if filename in {"", "."}:
        filename = CUSTOM_CATALOG_FILE
    if not filename.endswith((".yaml", ".yml")):
        filename = CUSTOM_CATALOG_FILE
    return root / filename


def _payload_sources(payload: dict[str, Any]) -> list[dict[str, Any]]:
    sources = payload.setdefault("sources", [])
    if not isinstance(sources, list):
        raise ValueError("catalog sources must be a list")
    return sources


def _find_source_payload(
    source_id: str,
    root: Path,
    *,
    preferred_file: str = "",
) -> tuple[Path, dict[str, Any], int, dict[str, Any]] | None:
    candidate_paths: list[Path] = []
    preferred_path = _catalog_path(preferred_file, root) if preferred_file else None
    if preferred_path and preferred_path.exists():
        candidate_paths.append(preferred_path)
    if root.exists():
        candidate_paths.extend(path for path in sorted(root.glob("*.yaml")) if path not in candidate_paths)
    for path in candidate_paths:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            continue
        for index, raw in enumerate(_payload_sources(payload)):
            if isinstance(raw, dict) and raw.get("id") == source_id:
                return path, payload, index, raw
    return None


def _write_catalog_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    for raw in _payload_sources(payload):
        SourceDefinitionIn.model_validate(raw)
    rendered = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(rendered, encoding="utf-8")
    reloaded = yaml.safe_load(tmp_path.read_text(encoding="utf-8")) or {}
    if not isinstance(reloaded, dict) or reloaded.get("schema_version") != 1:
        tmp_path.unlink(missing_ok=True)
        raise ValueError(f"{path} failed catalog validation")
    for raw in _payload_sources(reloaded):
        SourceDefinitionIn.model_validate(raw)
    os.replace(tmp_path, path)


def sync_source_catalog(db: Session, directory: str | Path | None = None) -> int:
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
    source.tagging = dumps(definition.tagging.model_dump(mode="json"))
    source.fetch = dumps(definition.fetch.model_dump(mode="json"))
    source.fulltext = dumps(definition.fulltext.model_dump(mode="json"))
    source.summary = dumps(summary.model_dump(mode="json") if summary else {})
    source.auth = dumps(auth.model_dump(mode="json"))
    source.auth_mode = auth.mode
    source.stability_level = definition.stability
    source.spec_json = spec_json
    source.spec_hash = hashlib.sha256(spec_json.encode("utf-8")).hexdigest()
    source.catalog_file = catalog_file
    source.enabled = False


def attempt_model(data: FetchAttemptIn, index: int) -> SourceAttempt:
    config: dict[str, Any] = {
        "timeout_seconds": data.timeout_seconds,
        "selectors": data.selectors,
        "limit": data.limit,
        "reader_fallback": data.reader_fallback,
        "exclude": data.exclude,
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
        tagging=normalize_tagging_config(loads(getattr(source, "tagging", ""), DEFAULT_TAGGING)),
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
