from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import math
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

import httpx
import yaml
from sqlalchemy import Select, and_, distinct, func, or_, select, update
from sqlalchemy.orm import Session, selectinload

from app.config import Settings, get_settings
from app.fulltext import extract_generic_article, strip_html
from app.job_queue import enqueue_embed_item, enqueue_summarize_item
from app.context import DEFAULT_PROFILE_ID
from app.models import ExternalTrendSignal, FeedPreset, Fulltext, Item, ItemEmbedding, ItemRecommendationScore, ItemSource, Job, JobStatus, LLMProvider, LLMUsageEvent, RawEntry, RecommendationRun, Setting, Source, SourceRun, SourceRuntime, SourceSubscription, Summary, SummaryStatus, UserItemEvent, UserItemState, UserPreference, utcnow
from app.schemas import ItemOut, SourceDefinitionIn, SourceDefinitionOut, SourceDefinitionPatch, SourceRuntimeOut
from app.source_catalog import apply_source_definition_patch, definition_from_source, sync_source_catalog, upsert_source_definition
from app.subscriptions import subscribed_source_ids
from app.summary import generate_tags_codex_cli, generate_tags_openai_compatible
from app.tags import merge_tags, normalize_tagging_config, sanitize_tags
from app.utils import canonicalize_url, dedupe_key_from_parts, dumps, extract_entities, loads, stable_hash, text_matches


LLM_TAG_MAX_PER_FETCH = 20
FEED_PRESETS_PATH = Path(__file__).resolve().parents[2] / "config" / "feed-presets.yaml"
RECOMMENDATION_PROFILE_ID = DEFAULT_PROFILE_ID
RECOMMENDATION_MODEL_VERSION = "hybrid-v1"
RECOMMENDATION_EMBEDDING_MODEL = "local-hash-v1"
RECOMMENDATION_SCORE_TTL_MINUTES = 45
RECOMMENDATION_WEIGHTS = {
    "personal_match": 35.0,
    "trend_breakthrough": 25.0,
    "source_importance": 20.0,
    "recency": 10.0,
    "quality": 10.0,
}

PRIORITY_TIERS: dict[str, tuple[int, int | None, str]] = {
    "p0": (0, 24, "P0 核心"),
    "p1": (25, 74, "P1 重要"),
    "p2": (75, 124, "P2 普通"),
    "p3": (125, None, "P3 低频"),
}


@dataclass(frozen=True)
class TaggingResult:
    tags: list[str]
    generated: bool = False
    attempted: bool = False


@dataclass(frozen=True)
class WorkIntent:
    kind: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class IngestResult:
    raw_count: int
    item_count: int
    fulltext_success_count: int
    touched_item_ids: set[str]
    work_intents: list[WorkIntent]


def effective_priority(source: Source) -> int:
    subscription = getattr(source, "subscription", None)
    override = subscription.priority_override if subscription else None
    return int(override if override is not None else source.priority)


def priority_tier(value: int | None) -> str:
    priority = int(value if value is not None else 100)
    for tier, (minimum, maximum, _label) in PRIORITY_TIERS.items():
        if priority >= minimum and (maximum is None or priority <= maximum):
            return tier
    return "p2"


def priority_tier_label(value: int | None) -> str:
    return PRIORITY_TIERS[priority_tier(value)][2]


def _priority_ranges(tiers: list[str] | None, priority_min: int | None, priority_max: int | None) -> list[tuple[int, int | None]]:
    ranges: list[tuple[int, int | None]] = []
    for tier in tiers or []:
        normalized = tier.lower()
        if normalized in PRIORITY_TIERS:
            minimum, maximum, _label = PRIORITY_TIERS[normalized]
            ranges.append((minimum, maximum))
    if priority_min is not None or priority_max is not None:
        ranges.append((0 if priority_min is None else priority_min, priority_max))
    return ranges


def _normalize_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, (tuple, set)):
        return [str(item) for item in value if str(item)]
    return [str(value)] if str(value) else []


def load_feed_preset_payload(path: str | Path = FEED_PRESETS_PATH) -> dict[str, Any]:
    preset_path = Path(path)
    if not preset_path.exists():
        return {"schema_version": 1, "presets": []}
    payload = yaml.safe_load(preset_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError(f"{preset_path} must declare schema_version: 1")
    presets = payload.get("presets", [])
    if not isinstance(presets, list):
        raise ValueError("feed presets must be a list")
    return payload


def sync_feed_presets(db: Session, path: str | Path = FEED_PRESETS_PATH) -> int:
    payload = load_feed_preset_payload(path)
    count = 0
    for index, raw in enumerate(payload.get("presets", [])):
        if not isinstance(raw, dict):
            continue
        preset_id = str(raw.get("id") or "").strip()
        name = str(raw.get("name") or preset_id).strip()
        if not preset_id or not name:
            continue
        preset = db.get(FeedPreset, preset_id)
        is_new = preset is None
        if is_new:
            preset = FeedPreset(id=preset_id, is_builtin=True)
            db.add(preset)
        preset.name = name
        preset.description = str(raw.get("description") or "")
        preset.is_builtin = True
        if is_new:
            preset.sort_order = int(raw.get("sort_order", index * 10) or 0)
        preset.filter_json = dumps(raw.get("filter") or {})
        preset.rank_json = dumps(raw.get("rank") or {"mode": "latest"})
        count += 1
    db.commit()
    return count


def feed_preset_to_dict(preset: FeedPreset) -> dict[str, Any]:
    return {
        "id": preset.id,
        "name": preset.name,
        "description": preset.description,
        "is_builtin": preset.is_builtin,
        "sort_order": preset.sort_order,
        "hidden": preset.hidden,
        "filter": loads(preset.filter_json, {}),
        "rank": loads(preset.rank_json, {"mode": "latest"}),
        "created_at": preset.created_at,
        "updated_at": preset.updated_at,
    }


def list_feed_presets(db: Session, include_hidden: bool = False) -> list[dict[str, Any]]:
    filters = [] if include_hidden else [FeedPreset.hidden.is_(False)]
    rows = db.execute(select(FeedPreset).where(*filters).order_by(FeedPreset.sort_order, FeedPreset.name)).scalars().all()
    return [feed_preset_to_dict(row) for row in rows]


def create_feed_preset(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    preset_id = _unique_preset_id(db, str(payload.get("id") or payload.get("name") or "custom"))
    preset = FeedPreset(
        id=preset_id,
        name=str(payload.get("name") or "自定义预设").strip() or "自定义预设",
        description=str(payload.get("description") or ""),
        is_builtin=False,
        sort_order=int(payload.get("sort_order", 100) or 100),
        hidden=bool(payload.get("hidden", False)),
        filter_json=dumps(payload.get("filter") or {}),
        rank_json=dumps(payload.get("rank") or {"mode": "latest"}),
    )
    db.add(preset)
    db.commit()
    db.refresh(preset)
    return feed_preset_to_dict(preset)


def patch_feed_preset(db: Session, preset_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    preset = db.get(FeedPreset, preset_id)
    if not preset:
        raise KeyError(preset_id)
    if preset.is_builtin:
        blocked = {"name", "description", "filter", "rank"} & {key for key, value in payload.items() if value is not None}
        if blocked:
            raise ValueError("Built-in presets only allow sort_order and hidden updates")
    if payload.get("name") is not None:
        preset.name = str(payload["name"]).strip() or preset.name
    if payload.get("description") is not None:
        preset.description = str(payload["description"])
    if payload.get("sort_order") is not None:
        preset.sort_order = int(payload["sort_order"])
    if payload.get("hidden") is not None:
        preset.hidden = bool(payload["hidden"])
    if payload.get("filter") is not None:
        preset.filter_json = dumps(payload["filter"])
    if payload.get("rank") is not None:
        preset.rank_json = dumps(payload["rank"])
    db.commit()
    db.refresh(preset)
    return feed_preset_to_dict(preset)


def delete_feed_preset(db: Session, preset_id: str) -> None:
    preset = db.get(FeedPreset, preset_id)
    if not preset:
        raise KeyError(preset_id)
    if preset.is_builtin:
        raise ValueError("Built-in presets cannot be deleted")
    db.delete(preset)
    db.commit()


def _unique_preset_id(db: Session, seed: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", seed.lower()).strip("-")
    if not slug:
        slug = f"custom-{uuid4().hex[:8]}"
    candidate = slug[:80]
    index = 2
    while db.get(FeedPreset, candidate):
        suffix = f"-{index}"
        candidate = f"{slug[: 80 - len(suffix)]}{suffix}"
        index += 1
    return candidate


def source_definition_to_out(source: Source, latest_run: SourceRun | None = None, stats: dict[str, Any] | None = None) -> SourceDefinitionOut:
    stats = stats or {}
    definition = definition_from_source(source)
    subscription = source.subscription
    runtime = source.runtime
    subscribed = bool(subscription and subscription.subscribed)
    display_priority = effective_priority(source)
    return SourceDefinitionOut(
        **definition.model_dump(),
        subscribed=subscribed,
        effective_priority=display_priority,
        priority_tier=priority_tier(display_priority),
        runtime=SourceRuntimeOut(
            last_run_at=runtime.last_run_at,
            last_success_at=runtime.last_success_at,
            failure_count=runtime.failure_count,
            empty_count=runtime.empty_count,
            last_error=runtime.last_error,
        )
        if runtime
        else None,
        latest_run=run_to_dict(latest_run) if latest_run else None,
        latest_item_published_at=stats.get("latest_item_published_at"),
        latest_item_ingested_at=stats.get("latest_item_ingested_at"),
        latest_item_title=str(stats.get("latest_item_title") or ""),
        content_audit=content_audit_for_source(source, latest_run, stats),
        spec_hash=source.spec_hash,
        catalog_file=source.catalog_file,
    )


def latest_ai_summary(db: Session | None, item_id: str) -> dict[str, Any] | None:
    if db is None:
        return None
    summary = db.execute(
        select(Summary)
        .where(Summary.item_id == item_id, Summary.status == SummaryStatus.ready.value)
        .order_by(Summary.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if not summary:
        return None
    data = loads(summary.data, None)
    return data if isinstance(data, dict) else None


def get_user_item_state(db: Session, item_id: str, profile_id: str = RECOMMENDATION_PROFILE_ID) -> UserItemState | None:
    return db.execute(
        select(UserItemState).where(UserItemState.profile_id == profile_id, UserItemState.item_id == item_id).limit(1)
    ).scalar_one_or_none()


def ensure_user_item_state(db: Session, item_id: str, profile_id: str = RECOMMENDATION_PROFILE_ID) -> UserItemState:
    state = get_user_item_state(db, item_id, profile_id)
    if state:
        return state
    state = UserItemState(profile_id=profile_id, item_id=item_id)
    db.add(state)
    db.flush()
    return state


def item_state_flags(db: Session | None, item: Item, profile_id: str = RECOMMENDATION_PROFILE_ID) -> dict[str, bool]:
    state = get_user_item_state(db, item.id, profile_id) if db else None
    return {
        "read": bool(state.read if state else False),
        "starred": bool(state.starred if state else False),
        "hidden": bool(state.hidden if state else False),
    }


def item_to_out(item: Item, db: Session | None = None) -> ItemOut:
    item_sources = item_sources_for_item(db, item) if db else []
    state = item_state_flags(db, item)
    return ItemOut(
        id=item.id,
        source_id=item.source_id,
        source_name=item.source_name,
        content_type=item.content_type,
        platform=item.platform,
        title=item.title,
        chinese_title=item.chinese_title,
        url=item.url,
        authors=loads(item.authors, []),
        published_at=item.published_at,
        summary=item.summary,
        raw_text=item.raw_text,
        ai_summary=latest_ai_summary(db, item.id),
        tags=loads(item.tags, []),
        entities=loads(item.entities, []),
        read=state["read"],
        starred=state["starred"],
        hidden=state["hidden"],
        summary_status=item.summary_status,
        recommendation_score=getattr(item, "_recommendation_score", None),
        recommendation_reasons=getattr(item, "_recommendation_reasons", []),
        recommendation_components=getattr(item, "_recommendation_components", {}),
        sources=item_sources,
    )


def item_sources_for_item(db: Session | None, item: Item) -> list[dict[str, Any]]:
    if db is None:
        return []
    rows = db.execute(
        select(ItemSource)
        .where(ItemSource.item_id == item.id)
        .order_by(ItemSource.first_seen_at, ItemSource.id)
    ).scalars().all()
    return [
        {
            "source_id": row.source_id,
            "source_name": row.source_name,
            "url": row.url,
            "tags": loads(row.tags, []),
        }
        for row in rows
    ]


def load_runtime_settings(db: Session) -> Settings:
    config_file_only_keys = {"rsshub_public_instances", "rsshub_self_hosted_base_url"}
    overrides: dict[str, Any] = {}
    for row in db.execute(select(Setting)).scalars():
        if row.key in config_file_only_keys:
            continue
        value = loads(row.value, None)
        overrides[row.key] = value
    settings = get_settings().model_copy(update={k: v for k, v in overrides.items() if v is not None})
    if settings.llm_provider_type == "openai_compatible":
        providers = list_llm_providers(db)
        primary = next((provider for provider in providers if provider.enabled and _provider_configured(provider)), None)
        if primary:
            settings = settings.model_copy(
                update={
                    "llm_base_url": primary.base_url,
                    "llm_api_key": primary.api_key,
                    "llm_model_name": primary.model_name,
                    "llm_temperature": _provider_temperature(primary),
                    "llm_timeout": primary.timeout,
                }
            )
        elif providers or _settings_flag(db, "llm_providers_initialized"):
            settings = settings.model_copy(update={"llm_base_url": None, "llm_api_key": None, "llm_model_name": None})
    return settings


def _settings_flag(db: Session, key: str) -> bool:
    row = db.get(Setting, key)
    return bool(row and loads(row.value, False))


def set_setting_value(db: Session, key: str, value: Any) -> None:
    stored = db.get(Setting, key)
    if not stored:
        stored = Setting(key=key)
        db.add(stored)
    stored.value = dumps(value)


def list_llm_providers(db: Session) -> list[LLMProvider]:
    return list(
        db.execute(
            select(LLMProvider)
            .where(LLMProvider.provider_type == "openai_compatible")
            .order_by(LLMProvider.priority, LLMProvider.id)
        ).scalars()
    )


def _provider_configured(provider: LLMProvider) -> bool:
    return bool(provider.base_url.strip() and provider.api_key.strip() and provider.model_name.strip())


def _provider_temperature(provider: LLMProvider) -> float:
    try:
        return float(provider.temperature)
    except (TypeError, ValueError):
        return 0.2


def ensure_initial_llm_provider(db: Session, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    if list_llm_providers(db) or _settings_flag(db, "llm_providers_initialized"):
        return False
    if settings.llm_provider_type != "openai_compatible":
        return False
    if not any([settings.llm_base_url, settings.llm_api_key, settings.llm_model_name]):
        return False
    provider = LLMProvider(
        name="Default API",
        provider_type="openai_compatible",
        base_url=settings.llm_base_url or "",
        api_key=settings.llm_api_key or "",
        model_name=settings.llm_model_name or "",
        temperature=str(settings.llm_temperature),
        timeout=settings.llm_timeout,
        enabled=settings.llm_configured,
        priority=0,
    )
    db.add(provider)
    set_setting_value(db, "llm_providers_initialized", True)
    db.commit()
    return True


def llm_provider_to_settings(settings: Settings, provider: LLMProvider) -> Settings:
    return settings.model_copy(
        update={
            "llm_provider_type": "openai_compatible",
            "llm_base_url": provider.base_url,
            "llm_api_key": provider.api_key,
            "llm_model_name": provider.model_name,
            "llm_temperature": _provider_temperature(provider),
            "llm_timeout": provider.timeout,
        }
    )


def openai_summary_provider_chain(db: Session, settings: Settings) -> list[tuple[LLMProvider | None, Settings]]:
    providers = [provider for provider in list_llm_providers(db) if provider.enabled and _provider_configured(provider)]
    if providers:
        return [(provider, llm_provider_to_settings(settings, provider)) for provider in providers]
    return []


def llm_provider_out(provider: LLMProvider) -> dict[str, Any]:
    return {
        "id": provider.id,
        "name": provider.name or "Custom API",
        "provider_type": provider.provider_type,
        "base_url": provider.base_url,
        "model_name": provider.model_name,
        "temperature": _provider_temperature(provider),
        "timeout": provider.timeout,
        "enabled": provider.enabled,
        "priority": provider.priority,
        "has_api_key": bool(provider.api_key),
        "last_error": provider.last_error,
        "created_at": provider.created_at,
        "updated_at": provider.updated_at,
    }


def run_to_dict(run: SourceRun | None) -> dict[str, Any] | None:
    if not run:
        return None
    return {
        "id": run.id,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "raw_count": run.raw_count,
        "item_count": run.item_count,
        "fulltext_success_count": run.fulltext_success_count,
        "summary_queued_count": run.summary_queued_count,
        "error_code": run.error_code,
        "error_message": run.error_message,
    }


def content_audit_for_source(source: Source, latest_run: SourceRun | None = None, stats: dict[str, Any] | None = None) -> dict[str, Any]:
    fulltext_config = loads(source.fulltext, {"mode": "feed_only"})
    mode = _fulltext_mode(fulltext_config)
    if latest_run and latest_run.status == "failed":
        status = "fetch_failed"
    elif not latest_run and not stats:
        status = "fetch_failed"
    elif source.content_type == "paper" and mode == "feed_only":
        status = "paper_abstract_only"
    elif stats:
        detail_count = int(stats.get("detail_count") or 0)
        feed_count = int(stats.get("feed_count") or 0)
        item_count = int(stats.get("item_count") or 0)
        avg_raw_len = float(stats.get("avg_raw_len") or 0)
        avg_summary_len = float(stats.get("avg_summary_len") or 0)
        max_raw_len = int(stats.get("max_raw_len") or 0)
        if item_count == 0:
            status = "fetch_failed"
        elif (
            latest_run
            and latest_run.status == "succeeded"
            and latest_run.raw_count
            and latest_run.fulltext_success_count / latest_run.raw_count >= 0.6
            and mode == "feed_only"
        ):
            status = "feed_fulltext"
        elif detail_count > 0 and max_raw_len >= 800:
            status = "detail_fulltext"
        elif avg_raw_len >= 1200 and (avg_summary_len >= 1200 or avg_raw_len > max(avg_summary_len * 2, 800)):
            status = "feed_fulltext"
        elif avg_raw_len <= 120:
            status = "feed_title_only"
        else:
            status = "feed_summary_only"
    elif mode in {"detail_only", "feed_then_detail"}:
        status = "detail_fulltext"
    else:
        status = "feed_summary_only"
    return {
        "status": status,
        "strategy": fulltext_config.get("strategy", mode),
        "mode": mode,
        "min_feed_fulltext_chars": fulltext_config.get("min_feed_chars", fulltext_config.get("min_feed_fulltext_chars")),
        "max_fulltext_per_run": fulltext_config.get("max_detail_pages_per_run", fulltext_config.get("max_fulltext_per_run")),
    }


def sync_default_source_pack(db: Session) -> None:
    sync_source_catalog(db)
    sync_feed_presets(db)


def seed_builtin_sources(db: Session) -> None:
    sync_default_source_pack(db)


def latest_runs(db: Session) -> dict[str, SourceRun]:
    subq = select(SourceRun.source_id, func.max(SourceRun.id).label("id")).group_by(SourceRun.source_id).subquery()
    rows = db.execute(select(SourceRun).join(subq, SourceRun.id == subq.c.id)).scalars().all()
    return {run.source_id: run for run in rows}


def source_content_stats(db: Session) -> dict[str, dict[str, Any]]:
    assoc = _item_source_assoc_subquery()
    item_rows = db.execute(
        select(
            assoc.c.source_id,
            func.count(distinct(Item.id)),
            func.avg(func.length(Item.summary)),
            func.avg(func.length(Item.raw_text)),
            func.min(func.length(Item.raw_text)),
            func.max(func.length(Item.raw_text)),
        )
        .join(Item, Item.id == assoc.c.item_id)
        .group_by(assoc.c.source_id)
    ).all()
    stats = {
        row[0]: {
            "item_count": int(row[1] or 0),
            "avg_summary_len": float(row[2] or 0),
            "avg_raw_len": float(row[3] or 0),
            "min_raw_len": int(row[4] or 0),
            "max_raw_len": int(row[5] or 0),
            "feed_count": 0,
            "detail_count": 0,
        }
        for row in item_rows
    }
    extractor_rows = db.execute(
        select(assoc.c.source_id, Fulltext.extractor, func.count(distinct(Fulltext.id)))
        .join(Item, Item.id == assoc.c.item_id)
        .join(Fulltext, Fulltext.item_id == Item.id)
        .where(Fulltext.status == "succeeded")
        .group_by(assoc.c.source_id, Fulltext.extractor)
    ).all()
    for source_id, extractor, count in extractor_rows:
        bucket = stats.setdefault(
            source_id,
            {
                "item_count": 0,
                "avg_summary_len": 0.0,
                "avg_raw_len": 0.0,
                "min_raw_len": 0,
                "max_raw_len": 0,
                "feed_count": 0,
                "detail_count": 0,
            },
        )
        if extractor == "generic_article":
            bucket["detail_count"] = int(count or 0)
        elif extractor == "feed_field":
            bucket["feed_count"] = int(count or 0)
    for source_id, latest in source_latest_item_stats(db).items():
        bucket = stats.setdefault(
            source_id,
            {
                "item_count": 0,
                "avg_summary_len": 0.0,
                "avg_raw_len": 0.0,
                "min_raw_len": 0,
                "max_raw_len": 0,
                "feed_count": 0,
                "detail_count": 0,
            },
        )
        bucket.update(latest)
    return stats


def source_latest_item_stats(db: Session) -> dict[str, dict[str, Any]]:
    ranked = (
        select(
            ItemSource.source_id.label("source_id"),
            Item.published_at.label("latest_item_published_at"),
            Item.created_at.label("latest_item_ingested_at"),
            Item.title.label("latest_item_title"),
            func.row_number()
            .over(
                partition_by=ItemSource.source_id,
                order_by=(Item.published_at.desc().nullslast(), Item.created_at.desc(), Item.id.desc()),
            )
            .label("rank"),
        )
        .join(Item, Item.id == ItemSource.item_id)
        .subquery()
    )
    rows = db.execute(
        select(
            ranked.c.source_id,
            ranked.c.latest_item_published_at,
            ranked.c.latest_item_ingested_at,
            ranked.c.latest_item_title,
        ).where(ranked.c.rank == 1)
    ).all()
    return {
        source_id: {
            "latest_item_published_at": latest_item_published_at,
            "latest_item_ingested_at": latest_item_ingested_at,
            "latest_item_title": latest_item_title or "",
        }
        for source_id, latest_item_published_at, latest_item_ingested_at, latest_item_title in rows
    }


def source_summary_stats(db: Session) -> dict[str, dict[str, int]]:
    assoc = _item_source_assoc_subquery()
    rows = db.execute(
        select(assoc.c.source_id, Item.summary_status, func.count(distinct(Item.id)))
        .join(Item, Item.id == assoc.c.item_id)
        .group_by(assoc.c.source_id, Item.summary_status)
    ).all()
    stats: dict[str, dict[str, int]] = {}
    for source_id, status, count in rows:
        bucket = stats.setdefault(source_id, {"ready": 0, "failed": 0, "pending": 0, "not_configured": 0, "skipped": 0})
        bucket[str(status)] = int(count or 0)
    return stats


def _item_source_assoc_subquery():
    return select(ItemSource.source_id.label("source_id"), ItemSource.item_id.label("item_id")).subquery()


def _summary_usage_bucket(db: Session, cutoff: datetime | None = None, model: str | None = None) -> dict[str, Any]:
    summary_filters = [Summary.provider == "openai_compatible"]
    event_filters = [LLMUsageEvent.provider == "openai_compatible"]
    if cutoff is not None:
        summary_filters.append(Summary.created_at >= cutoff)
        event_filters.append(LLMUsageEvent.created_at >= cutoff)
    if model is not None:
        summary_filters.append(Summary.model == model)
        event_filters.append(LLMUsageEvent.model == model)
    summary_status_rows = db.execute(select(Summary.status, func.count(Summary.id)).where(*summary_filters).group_by(Summary.status)).all()
    event_status_rows = db.execute(
        select(LLMUsageEvent.status, func.count(LLMUsageEvent.id)).where(*event_filters).group_by(LLMUsageEvent.status)
    ).all()
    status_counts: dict[str, int] = {}
    for status, count in [*summary_status_rows, *event_status_rows]:
        key = str(status)
        status_counts[key] = status_counts.get(key, 0) + int(count or 0)
    summary_token_row = db.execute(
        select(
            func.coalesce(func.sum(Summary.prompt_tokens), 0),
            func.coalesce(func.sum(Summary.completion_tokens), 0),
            func.coalesce(func.sum(Summary.total_tokens), 0),
            func.coalesce(func.sum(Summary.reasoning_tokens), 0),
            func.coalesce(func.sum(Summary.duration_ms), 0),
        ).where(*summary_filters)
    ).one()
    event_token_row = db.execute(
        select(
            func.coalesce(func.sum(LLMUsageEvent.prompt_tokens), 0),
            func.coalesce(func.sum(LLMUsageEvent.completion_tokens), 0),
            func.coalesce(func.sum(LLMUsageEvent.total_tokens), 0),
            func.coalesce(func.sum(LLMUsageEvent.reasoning_tokens), 0),
            func.coalesce(func.sum(LLMUsageEvent.duration_ms), 0),
        ).where(*event_filters)
    ).one()
    requests = sum(status_counts.values())
    return {
        "requests": requests,
        "success": status_counts.get(SummaryStatus.ready.value, 0),
        "failed": status_counts.get(SummaryStatus.failed.value, 0),
        "prompt_tokens": int(summary_token_row[0] or 0) + int(event_token_row[0] or 0),
        "completion_tokens": int(summary_token_row[1] or 0) + int(event_token_row[1] or 0),
        "total_tokens": int(summary_token_row[2] or 0) + int(event_token_row[2] or 0),
        "reasoning_tokens": int(summary_token_row[3] or 0) + int(event_token_row[3] or 0),
        "duration_ms": int(summary_token_row[4] or 0) + int(event_token_row[4] or 0),
    }


def llm_usage_stats(db: Session) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    last_summary_used_at = db.execute(select(func.max(Summary.created_at)).where(Summary.provider == "openai_compatible")).scalar_one()
    last_event_used_at = db.execute(
        select(func.max(LLMUsageEvent.created_at)).where(LLMUsageEvent.provider == "openai_compatible")
    ).scalar_one()
    last_used_candidates = [value for value in [last_summary_used_at, last_event_used_at] if value is not None]
    last_summary_error = db.execute(
        select(Summary)
        .where(Summary.provider == "openai_compatible", Summary.error_message != "")
        .order_by(Summary.created_at.desc(), Summary.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    last_event_error = db.execute(
        select(LLMUsageEvent)
        .where(LLMUsageEvent.provider == "openai_compatible", LLMUsageEvent.error_message != "")
        .order_by(LLMUsageEvent.created_at.desc(), LLMUsageEvent.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    last_errors = [error for error in [last_summary_error, last_event_error] if error is not None]
    last_error = max(last_errors, key=lambda row: row.created_at) if last_errors else None
    summary_model_rows = db.execute(
        select(Summary.model)
        .where(Summary.provider == "openai_compatible")
        .group_by(Summary.model)
        .order_by(Summary.model)
    ).all()
    event_model_rows = db.execute(
        select(LLMUsageEvent.model)
        .where(LLMUsageEvent.provider == "openai_compatible")
        .group_by(LLMUsageEvent.model)
        .order_by(LLMUsageEvent.model)
    ).all()
    models = sorted({model or "" for (model,) in [*summary_model_rows, *event_model_rows]})
    return {
        "provider": "openai_compatible",
        "all_time": _summary_usage_bucket(db),
        "recent_24h": _summary_usage_bucket(db, now - timedelta(days=1)),
        "recent_7d": _summary_usage_bucket(db, now - timedelta(days=7)),
        "by_model": [
            {"model": model or "unknown", **_summary_usage_bucket(db, model=model)}
            for model in models
        ],
        "last_used_at": max(last_used_candidates) if last_used_candidates else None,
        "last_error_at": last_error.created_at if last_error else None,
        "last_error": last_error.error_message if last_error else "",
    }


def list_source_definitions(db: Session) -> list[SourceDefinitionOut]:
    runs = latest_runs(db)
    stats = source_content_stats(db)
    sources = (
        db.execute(
            select(Source)
            .options(selectinload(Source.attempts), selectinload(Source.subscription), selectinload(Source.runtime))
            .order_by(Source.group, Source.priority, Source.name)
        )
        .scalars()
        .all()
    )
    return [source_definition_to_out(source, runs.get(source.id), stats.get(source.id)) for source in sources]


def create_source_definition(db: Session, definition: SourceDefinitionIn, subscribe: bool = True) -> SourceDefinitionOut:
    if db.get(Source, definition.id):
        raise ValueError("Source id already exists")
    source = upsert_source_definition(db, definition, catalog_file="db", builtin=False)
    if subscribe:
        db.add(SourceSubscription(source_id=source.id, profile_id=RECOMMENDATION_PROFILE_ID, subscribed=True))
    db.commit()
    db.refresh(source)
    return source_definition_to_out(source)


def patch_source_definition(db: Session, source: Source, patch: SourceDefinitionPatch) -> SourceDefinitionOut:
    current_definition = definition_from_source(source)
    updated_definition = apply_source_definition_patch(current_definition, patch)
    upsert_source_definition(db, updated_definition, catalog_file="db", builtin=False)
    db.commit()
    db.refresh(source)
    return source_definition_to_out(source)


AUTO_SUMMARY_QUEUE_STATUSES = {
    SummaryStatus.not_configured.value,
    SummaryStatus.skipped.value,
    SummaryStatus.pending.value,
}


def _aware_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _auto_summary_cutoff(source: Source) -> datetime:
    days = max(1, int(source.auto_summary_days or 7))
    return datetime.now(timezone.utc) - timedelta(days=days)


def _item_is_in_auto_summary_window(item: Item, source: Source) -> bool:
    reference = _aware_datetime(item.published_at) or _aware_datetime(item.created_at)
    return bool(reference and reference >= _auto_summary_cutoff(source))


def _item_has_ready_summary(db: Session, item_id: str) -> bool:
    return bool(
        db.execute(
            select(Summary.id)
            .where(Summary.item_id == item_id, Summary.status == SummaryStatus.ready.value)
            .limit(1)
        ).scalar_one_or_none()
    )


def _prepare_auto_summary_item(db: Session, source: Source, item: Item, settings: Settings) -> bool:
    if item.summary_status in {SummaryStatus.ready.value, SummaryStatus.failed.value}:
        return False
    if _item_has_ready_summary(db, item.id):
        item.summary_status = SummaryStatus.ready.value
        return False
    if not settings.llm_configured:
        item.summary_status = SummaryStatus.not_configured.value
        return False
    if (
        source.auto_summary_enabled
        and item.summary_status in AUTO_SUMMARY_QUEUE_STATUSES
        and item.raw_text.strip()
        and _item_is_in_auto_summary_window(item, source)
    ):
        item.summary_status = SummaryStatus.pending.value
        return True
    if item.summary_status in {SummaryStatus.not_configured.value, SummaryStatus.skipped.value}:
        item.summary_status = SummaryStatus.skipped.value
    return False


def queue_auto_summaries(db: Session, settings: Settings, source_id: str | None = None, limit: int = 20) -> int:
    if not settings.llm_configured or limit <= 0:
        return 0
    source_stmt = (
        select(Source)
        .join(SourceSubscription, SourceSubscription.source_id == Source.id)
        .where(
            Source.auto_summary_enabled.is_(True),
            SourceSubscription.profile_id == RECOMMENDATION_PROFILE_ID,
            SourceSubscription.subscribed.is_(True),
        )
    )
    if source_id:
        source_stmt = source_stmt.where(Source.id == source_id)
    sources = db.execute(source_stmt.order_by(Source.group, Source.priority)).scalars().all()
    queued = 0
    for source in sources:
        remaining = limit - queued
        if remaining <= 0:
            break
        active_item_ids = _active_summary_job_item_ids(db, source.id)
        cutoff = _auto_summary_cutoff(source)
        ready_exists = select(Summary.id).where(Summary.item_id == Item.id, Summary.status == SummaryStatus.ready.value).exists()
        item_stmt = (
            select(Item)
            .where(
                _item_has_source([source.id]),
                Item.summary_status.in_(AUTO_SUMMARY_QUEUE_STATUSES),
                func.trim(Item.raw_text) != "",
                or_(Item.published_at >= cutoff, and_(Item.published_at.is_(None), Item.created_at >= cutoff)),
                ~ready_exists,
            )
        )
        if active_item_ids:
            item_stmt = item_stmt.where(Item.id.not_in(active_item_ids))
        item_stmt = item_stmt.order_by(Item.published_at.desc().nullslast(), Item.created_at.desc()).limit(remaining)
        for item in db.execute(item_stmt).scalars().all():
            if item.id in active_item_ids:
                continue
            if not item.raw_text.strip():
                continue
            item.summary_status = SummaryStatus.pending.value
            enqueue_summarize_item(db, item.id)
            active_item_ids.add(item.id)
            queued += 1
    if queued:
        db.commit()
    return queued


def _active_summary_job_item_ids(db: Session, source_id: str) -> set[str]:
    item_ids = set(db.execute(select(Item.id).where(_item_has_source([source_id]))).scalars())
    if not item_ids:
        return set()
    active_jobs = db.execute(
        select(Job).where(
            Job.type == "summarize_item",
            Job.status.in_([JobStatus.queued.value, JobStatus.running.value, JobStatus.retrying.value]),
        )
    ).scalars()
    active_item_ids: set[str] = set()
    for job in active_jobs:
        payload = loads(job.payload, {})
        item_id = payload.get("item_id")
        if isinstance(item_id, str) and item_id in item_ids:
            active_item_ids.add(item_id)
    return active_item_ids


def reconcile_auto_summary_statuses(db: Session, settings: Settings, limit: int = 5000) -> int:
    if not settings.llm_configured or limit <= 0:
        return 0
    changed = 0
    sources = db.execute(select(Source).order_by(Source.group, Source.priority)).scalars().all()
    for source in sources:
        remaining = limit - changed
        if remaining <= 0:
            break
        filters = [
            _item_has_source([source.id]),
            Item.summary_status == SummaryStatus.not_configured.value,
        ]
        if source.auto_summary_enabled:
            cutoff = _auto_summary_cutoff(source)
            filters.append(
                or_(
                    Item.raw_text == "",
                    and_(Item.published_at.is_not(None), Item.published_at < cutoff),
                    and_(Item.published_at.is_(None), Item.created_at < cutoff),
                )
            )
        items = db.execute(
            select(Item)
            .where(*filters)
            .limit(remaining)
        ).scalars().all()
        for item in items:
            if _item_has_ready_summary(db, item.id):
                item.summary_status = SummaryStatus.ready.value
                changed += 1
                continue
            item.summary_status = SummaryStatus.skipped.value
            changed += 1
    if changed:
        db.commit()
    return changed


def resolve_item_query_params(
    db: Session,
    *,
    preset_id: str | None = None,
    source_id: list[str] | None = None,
    source_group: list[str] | str | None = None,
    platform: list[str] | str | None = None,
    q: str | None = None,
    since: str | None = None,
    summary_status: str | None = None,
    read: bool | None = None,
    starred: bool | None = None,
    hidden: bool | None = None,
    priority_tier: list[str] | str | None = None,
    priority_min: int | None = None,
    priority_max: int | None = None,
    rank: str | None = None,
) -> dict[str, Any]:
    resolved: dict[str, Any] = {
        "source_id": None,
        "source_group": None,
        "platform": None,
        "q": None,
        "since": None,
        "summary_status": None,
        "read": None,
        "starred": None,
        "hidden": False,
        "priority_tier": None,
        "priority_min": None,
        "priority_max": None,
        "rank": "latest",
    }
    if preset_id:
        preset = db.get(FeedPreset, preset_id)
        if not preset:
            raise KeyError(preset_id)
        preset_filter = loads(preset.filter_json, {})
        preset_rank = loads(preset.rank_json, {"mode": "latest"})
        resolved.update(
            {
                "source_id": _normalize_list(preset_filter.get("source_ids") or preset_filter.get("source_id")) or None,
                "source_group": _normalize_list(preset_filter.get("groups") or preset_filter.get("source_group")) or None,
                "platform": _normalize_list(preset_filter.get("platforms") or preset_filter.get("platform")) or None,
                "q": preset_filter.get("q"),
                "since": preset_filter.get("since"),
                "summary_status": preset_filter.get("summary_status"),
                "read": preset_filter.get("read"),
                "starred": preset_filter.get("starred"),
                "hidden": preset_filter.get("hidden", False),
                "priority_tier": _normalize_list(preset_filter.get("priority_tiers") or preset_filter.get("priority_tier")) or None,
                "priority_min": preset_filter.get("priority_min"),
                "priority_max": preset_filter.get("priority_max"),
                "rank": str(preset_rank.get("mode") or "latest"),
            }
        )
    overrides = {
        "source_id": source_id,
        "source_group": source_group,
        "platform": platform,
        "q": q,
        "since": since,
        "summary_status": summary_status,
        "read": read,
        "starred": starred,
        "hidden": hidden,
        "priority_tier": priority_tier,
        "priority_min": priority_min,
        "priority_max": priority_max,
        "rank": rank,
    }
    for key, value in overrides.items():
        if value is None:
            continue
        if key in {"source_id", "source_group", "platform", "priority_tier"}:
            resolved[key] = _normalize_list(value) or None
        else:
            resolved[key] = value
    if resolved["hidden"] is None:
        resolved["hidden"] = False
    if resolved["rank"] not in {"latest", "recommended", "for_you"}:
        resolved["rank"] = "latest"
    return resolved


def query_items(
    db: Session,
    source_id: list[str] | None = None,
    source_group: list[str] | str | None = None,
    platform: list[str] | str | None = None,
    include_unsubscribed: bool = False,
    q: str | None = None,
    since: str | None = None,
    summary_status: str | None = None,
    read: bool | None = None,
    starred: bool | None = None,
    hidden: bool | None = False,
    priority_tier: list[str] | str | None = None,
    priority_min: int | None = None,
    priority_max: int | None = None,
    rank: str = "latest",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Item], int]:
    stmt: Select = select(Item)
    filters = []
    if not include_unsubscribed:
        subscribed_ids = subscribed_source_ids(db)
        if not subscribed_ids:
            return [], 0
        filters.append(_item_has_source(subscribed_ids))
    if source_id:
        filters.append(_item_has_source(source_id))
    platforms = _normalize_list(platform)
    if platforms:
        filters.append(
            select(ItemSource.id)
            .join(Source, Source.id == ItemSource.source_id)
            .where(ItemSource.item_id == Item.id, Source.platform.in_(platforms))
            .exists()
        )
    priority_ranges = _priority_ranges(_normalize_list(priority_tier), priority_min, priority_max)
    if priority_ranges:
        filters.append(_item_has_priority(priority_ranges))
    if summary_status:
        filters.append(Item.summary_status == summary_status)
    if read is not None:
        filters.append(_item_state_filter("read", read))
    if starred is not None:
        filters.append(_item_state_filter("starred", starred))
    if hidden is not None:
        filters.append(_item_state_filter("hidden", hidden))
    if since:
        days = {"today": 1, "3d": 3, "7d": 7}.get(since)
        if days:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            filters.append(or_(Item.published_at >= cutoff, and_(Item.published_at.is_(None), Item.created_at >= cutoff)))
    elif rank == "for_you":
        now = datetime.now(timezone.utc)
        recent_cutoff = now - timedelta(days=30)
        extended_cutoff = now - timedelta(days=90)
        filters.append(
            or_(
                Item.published_at >= recent_cutoff,
                and_(Item.published_at.is_(None), Item.created_at >= recent_cutoff),
                and_(
                    _item_state_filter("read", False),
                    _item_has_priority([(0, 74)]),
                    or_(Item.published_at >= extended_cutoff, and_(Item.published_at.is_(None), Item.created_at >= extended_cutoff)),
                ),
            )
        )
    if q:
        term = f"%{q}%"
        filters.append(
            or_(
                Item.title.ilike(term),
                Item.chinese_title.ilike(term),
                Item.summary.ilike(term),
                Item.raw_text.ilike(term),
                Item.authors.ilike(term),
                Item.source_name.ilike(term),
                Item.tags.ilike(term),
                select(ItemSource.id)
                .where(
                    ItemSource.item_id == Item.id,
                    or_(ItemSource.source_name.ilike(term), ItemSource.tags.ilike(term)),
                )
                .exists(),
            )
        )
    source_groups = _normalize_list(source_group)
    if source_groups:
        filters.append(
            select(ItemSource.id)
            .join(Source, Source.id == ItemSource.source_id)
            .where(ItemSource.item_id == Item.id, Source.group.in_(source_groups))
            .exists()
        )
    if filters:
        stmt = stmt.where(and_(*filters))
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.execute(count_stmt).scalar_one()
    if rank in {"recommended", "for_you"}:
        rows = db.execute(stmt.order_by(Item.published_at.desc().nullslast(), Item.created_at.desc(), Item.id.desc())).scalars().all()
        profile = recommendation_profile_weights(db)
        if rank == "for_you":
            _attach_recommendation_runtime_caches(db, profile)
        cached_scores = _cached_recommendation_scores(db, [item.id for item in rows]) if rank == "for_you" else {}
        for item in rows:
            cached = cached_scores.get(item.id)
            if rank == "for_you" and cached:
                score = float(cached["score"])
                reasons = cached["reasons"]
                components = cached["components"]
            else:
                score, reasons, components = recommendation_for_item(db, item, profile, mode=rank)
            setattr(item, "_recommendation_score", score)
            setattr(item, "_recommendation_reasons", reasons)
            setattr(item, "_recommendation_components", components)
        rows.sort(key=lambda item: (getattr(item, "_recommendation_score", 0.0), _sort_datetime(item), item.id), reverse=True)
        return rows[offset : offset + limit], total
    rows = db.execute(stmt.order_by(Item.published_at.desc().nullslast(), Item.created_at.desc()).offset(offset).limit(limit)).scalars().all()
    return rows, total


def _item_has_source(source_ids: list[str]) -> Any:
    return select(ItemSource.id).where(ItemSource.item_id == Item.id, ItemSource.source_id.in_(source_ids)).exists()


def _item_state_filter(field: str, expected: bool, profile_id: str = RECOMMENDATION_PROFILE_ID) -> Any:
    column = getattr(UserItemState, field)
    exists = (
        select(UserItemState.id)
        .where(UserItemState.profile_id == profile_id, UserItemState.item_id == Item.id, column.is_(True))
        .exists()
    )
    return exists if expected else ~exists


def _item_has_priority(ranges: list[tuple[int, int | None]]) -> Any:
    priority_value = func.coalesce(SourceSubscription.priority_override, Source.priority)
    range_filters = []
    for minimum, maximum in ranges:
        criteria = [priority_value >= minimum]
        if maximum is not None:
            criteria.append(priority_value <= maximum)
        range_filters.append(and_(*criteria))
    return (
        select(ItemSource.id)
        .join(Source, Source.id == ItemSource.source_id)
        .outerjoin(
            SourceSubscription,
            and_(SourceSubscription.source_id == Source.id, SourceSubscription.profile_id == RECOMMENDATION_PROFILE_ID),
        )
        .where(ItemSource.item_id == Item.id, or_(*range_filters))
        .exists()
    )


def _sort_datetime(item: Item) -> datetime:
    value = item.published_at or item.created_at
    return _aware_datetime(value) or datetime.min.replace(tzinfo=timezone.utc)


def record_item_event(
    db: Session,
    item_id: str,
    event_type: str,
    source_id: str = "",
    metadata: dict[str, Any] | None = None,
    *,
    profile_id: str = RECOMMENDATION_PROFILE_ID,
    commit: bool = True,
) -> dict[str, Any]:
    item = db.get(Item, item_id)
    if not item:
        raise KeyError(item_id)
    event = UserItemEvent(profile_id=profile_id, item_id=item_id, event_type=event_type, source_id=source_id or "", metadata_json=dumps(metadata or {}))
    db.add(event)
    if event_type in {"open", "read", "unread", "star", "unstar", "hide", "unhide", "more_like_this", "less_like_this", "dismiss"}:
        _apply_event_to_implicit_profile(db, item, event_type, profile_id)
        _expire_recommendation_scores(db, profile_id=profile_id)
    else:
        _expire_recommendation_scores(db, item_id=item_id)
    if commit:
        db.commit()
        db.refresh(event)
    else:
        db.flush()
    return item_event_to_dict(event)


def item_event_to_dict(event: UserItemEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "item_id": event.item_id,
        "event_type": event.event_type,
        "source_id": event.source_id,
        "metadata": loads(event.metadata_json, {}),
        "created_at": event.created_at,
    }


def recommendation_for_item(
    db: Session,
    item: Item,
    profile: dict[str, dict[str, float]] | None = None,
    *,
    mode: str = "recommended",
) -> tuple[float, list[str], dict[str, float]]:
    profile = profile or {}
    reasons: list[str] = []
    source_rows = _item_source_priority_rows(db, item)
    source_score, source_reasons = _source_importance_score(source_rows)
    personal_score, personal_reasons = _personal_match_score(item, source_rows, profile)
    recency_score, recency_reasons = _recency_score(item)
    quality_score, quality_reasons = _quality_score(item)
    trend_score, trend_reasons = _trend_breakthrough_score(db, item, profile) if mode == "for_you" else (0.0, [])
    penalty_score, penalty_reasons = _interaction_penalty(db, item)
    components = {
        "personal_match": round(personal_score, 3),
        "trend_breakthrough": round(trend_score, 3),
        "source_importance": round(source_score, 3),
        "recency": round(recency_score, 3),
        "quality": round(quality_score, 3),
        "interaction_penalty": round(penalty_score, 3),
    }
    total = _weighted_recommendation_total(components, profile)
    for bucket in [personal_reasons, trend_reasons, source_reasons, recency_reasons, quality_reasons, penalty_reasons]:
        for reason in bucket:
            if reason not in reasons:
                reasons.append(reason)
    return round(total, 3), reasons[:6], components


def _weighted_recommendation_total(components: dict[str, float], profile: dict[str, dict[str, float]]) -> float:
    configured = profile.get("weights", {})
    total = 0.0
    for key, default_weight in RECOMMENDATION_WEIGHTS.items():
        value = float(components.get(key, 0.0) or 0.0)
        if default_weight <= 0:
            total += value
            continue
        target_weight = max(0.0, min(float(configured.get(key, default_weight) or default_weight), 100.0))
        total += (value / default_weight) * target_weight
    total += float(components.get("interaction_penalty", 0.0) or 0.0)
    return total


def _item_source_priority_rows(db: Session, item: Item) -> list[dict[str, Any]]:
    rows = db.execute(
        select(ItemSource, Source, SourceSubscription)
        .join(Source, Source.id == ItemSource.source_id)
        .outerjoin(
            SourceSubscription,
            and_(SourceSubscription.source_id == Source.id, SourceSubscription.profile_id == RECOMMENDATION_PROFILE_ID),
        )
        .where(ItemSource.item_id == item.id)
    ).all()
    return [
        {
            "source_id": item_source.source_id,
            "source_name": item_source.source_name or source.name,
            "platform": source.platform,
            "priority": int(subscription.priority_override if subscription and subscription.priority_override is not None else source.priority),
        }
        for item_source, source, subscription in rows
    ]


def recommendation_profile_weights(db: Session, profile_id: str = RECOMMENDATION_PROFILE_ID) -> dict[str, dict[str, float]]:
    profile = _empty_profile_weights()
    row = db.get(UserPreference, profile_id)
    if row:
        explicit = loads(row.explicit_json, {})
        implicit = loads(row.implicit_json, {})
        for bucket, values in explicit.items():
            if bucket not in profile:
                continue
            for value in _normalize_list(values):
                _bump(profile[bucket], value, 8.0)
        for bucket, weights in implicit.items():
            if bucket not in profile or not isinstance(weights, dict):
                continue
            for key, weight in weights.items():
                _bump(profile[bucket], key, float(weight or 0.0))
        excluded = loads(row.excluded_json, {})
        profile["excluded_terms"] = {str(value).strip().lower(): -20.0 for value in _normalize_list(excluded.get("terms"))}
        settings = loads(row.settings_json, {})
        profile["trend_providers"] = {str(value).strip().lower(): 1.0 for value in _normalize_list(settings.get("trend_providers")) or ["hn"]}
        profile["weights"] = {str(key): float(value) for key, value in (settings.get("weights") or {}).items() if isinstance(value, (int, float))}
    return profile


def get_recommendation_profile(db: Session, profile_id: str = RECOMMENDATION_PROFILE_ID) -> dict[str, Any]:
    row = db.get(UserPreference, profile_id)
    if not row:
        return {
            "profile_id": profile_id,
            "interests": [],
            "excluded_terms": [],
            "source_ids": [],
            "tags": [],
            "entities": [],
            "platforms": [],
            "content_types": [],
            "trend_providers": ["hn"],
            "weights": RECOMMENDATION_WEIGHTS,
            "implicit": {},
            "updated_at": None,
        }
    explicit = loads(row.explicit_json, {})
    excluded = loads(row.excluded_json, {})
    settings = loads(row.settings_json, {})
    return {
        "profile_id": row.profile_id,
        "interests": _normalize_list(explicit.get("interests")),
        "excluded_terms": _normalize_list(excluded.get("terms")),
        "source_ids": _normalize_list(explicit.get("sources")),
        "tags": _normalize_list(explicit.get("tags")),
        "entities": _normalize_list(explicit.get("entities")),
        "platforms": _normalize_list(explicit.get("platforms")),
        "content_types": _normalize_list(explicit.get("content_types")),
        "trend_providers": _normalize_list(settings.get("trend_providers")) or ["hn"],
        "weights": {**RECOMMENDATION_WEIGHTS, **(settings.get("weights") or {})},
        "implicit": loads(row.implicit_json, {}),
        "updated_at": row.updated_at,
    }


def patch_recommendation_profile(db: Session, payload: dict[str, Any], profile_id: str = RECOMMENDATION_PROFILE_ID) -> dict[str, Any]:
    row = _ensure_user_preference(db, profile_id)
    explicit = loads(row.explicit_json, {})
    excluded = loads(row.excluded_json, {})
    settings = loads(row.settings_json, {})
    mapping = {
        "interests": "interests",
        "source_ids": "sources",
        "tags": "tags",
        "entities": "entities",
        "platforms": "platforms",
        "content_types": "content_types",
    }
    for public_key, stored_key in mapping.items():
        if public_key in payload and payload[public_key] is not None:
            explicit[stored_key] = _dedupe_values(payload[public_key])
    if "excluded_terms" in payload and payload["excluded_terms"] is not None:
        excluded["terms"] = _dedupe_values(payload["excluded_terms"])
    if "trend_providers" in payload and payload["trend_providers"] is not None:
        settings["trend_providers"] = _dedupe_values(payload["trend_providers"])
    if "weights" in payload and payload["weights"] is not None:
        settings["weights"] = {str(key): float(value) for key, value in payload["weights"].items()}
    row.explicit_json = dumps(explicit)
    row.excluded_json = dumps(excluded)
    row.settings_json = dumps(settings)
    _expire_recommendation_scores(db, profile_id=profile_id)
    db.commit()
    db.refresh(row)
    return get_recommendation_profile(db, profile_id)


def _ensure_user_preference(db: Session, profile_id: str = RECOMMENDATION_PROFILE_ID) -> UserPreference:
    row = db.get(UserPreference, profile_id)
    if row:
        return row
    row = UserPreference(
        profile_id=profile_id,
        explicit_json=dumps({}),
        implicit_json=dumps({}),
        excluded_json=dumps({"terms": []}),
        settings_json=dumps({"trend_providers": ["hn"], "weights": RECOMMENDATION_WEIGHTS}),
    )
    db.add(row)
    db.flush()
    return row


def _empty_profile_weights() -> dict[str, dict[str, float]]:
    return {
        "tags": {},
        "entities": {},
        "sources": {},
        "platforms": {},
        "content_types": {},
        "interests": {},
        "excluded_terms": {},
        "trend_providers": {},
        "weights": {},
    }


def _event_preference_profile(db: Session, profile_id: str = RECOMMENDATION_PROFILE_ID) -> dict[str, dict[str, float]]:
    profile = _empty_profile_weights()
    rows = db.execute(
        select(UserItemEvent, Item)
        .join(Item, Item.id == UserItemEvent.item_id)
        .where(
            UserItemEvent.profile_id == profile_id,
            UserItemEvent.event_type.in_(["open", "read", "star", "hide", "more_like_this", "less_like_this", "dismiss"]),
        )
        .order_by(UserItemEvent.created_at.desc(), UserItemEvent.id.desc())
        .limit(500)
    ).all()
    weights = {"star": 6.0, "more_like_this": 4.5, "open": 1.5, "read": 1.0, "less_like_this": -5.0, "hide": -8.0, "dismiss": -8.0}
    now = datetime.now(timezone.utc)
    for event, item in rows:
        weight = weights.get(event.event_type, 0.0)
        if not weight:
            continue
        event_time = _aware_datetime(event.created_at) or now
        age_days = max((now - event_time).total_seconds() / 86400, 0)
        weight *= max(0.2, math.exp(-age_days / 30.0))
        for tag in loads(item.tags, []):
            _bump(profile["tags"], tag, weight)
        for entity in loads(item.entities, []):
            _bump(profile["entities"], entity, weight)
        _bump(profile["platforms"], item.platform, weight)
        _bump(profile["content_types"], item.content_type, weight)
        for source in item_sources_for_item(db, item):
            _bump(profile["sources"], source.get("source_id", ""), weight)
    return profile


def _apply_event_to_implicit_profile(db: Session, item: Item, event_type: str, profile_id: str = RECOMMENDATION_PROFILE_ID) -> None:
    row = _ensure_user_preference(db, profile_id)
    implicit = loads(row.implicit_json, {})
    weights = {
        "star": 4.0,
        "unstar": -4.0,
        "more_like_this": 3.0,
        "open": 0.8,
        "read": 0.5,
        "unread": -0.5,
        "less_like_this": -4.0,
        "hide": -6.0,
        "unhide": 6.0,
        "dismiss": -7.0,
    }
    weight = weights.get(event_type, 0.0)
    if not weight:
        return
    for bucket in ["tags", "entities", "sources", "platforms", "content_types"]:
        implicit.setdefault(bucket, {})
    for tag in loads(item.tags, []):
        _bump(implicit["tags"], tag, weight)
    for entity in loads(item.entities, []):
        _bump(implicit["entities"], entity, weight)
    _bump(implicit["platforms"], item.platform, weight)
    _bump(implicit["content_types"], item.content_type, weight)
    for source in item_sources_for_item(db, item):
        _bump(implicit["sources"], source.get("source_id", ""), weight)
    row.implicit_json = dumps(_clamp_profile_weights(implicit))


def _clamp_profile_weights(profile: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    clamped: dict[str, dict[str, float]] = {}
    for bucket, values in profile.items():
        if not isinstance(values, dict):
            continue
        clamped[bucket] = {
            key: round(max(min(float(value or 0.0), 30.0), -30.0), 3)
            for key, value in values.items()
            if str(key).strip() and abs(float(value or 0.0)) >= 0.05
        }
    return clamped


def _bump(bucket: dict[str, float], key: str, weight: float) -> None:
    normalized = str(key or "").strip().lower()
    if normalized:
        bucket[normalized] = bucket.get(normalized, 0.0) + weight


def _weighted_matches(values: list[str], weights: dict[str, float], limit: int) -> list[tuple[str, float]]:
    matches = []
    for value in values:
        normalized = str(value or "").strip().lower()
        weight = weights.get(normalized, 0.0)
        if weight > 0:
            matches.append((str(value), min(weight, 12.0)))
    matches.sort(key=lambda row: row[1], reverse=True)
    return matches[:limit]


def _source_importance_score(source_rows: list[dict[str, Any]]) -> tuple[float, list[str]]:
    if not source_rows:
        return 6.0, []
    best = min(source_rows, key=lambda row: row["priority"])
    tier = priority_tier(best["priority"])
    score = {"p0": 20.0, "p1": 14.0, "p2": 8.0, "p3": 3.0}.get(tier, 6.0)
    return score, [f"{PRIORITY_TIERS[tier][2]} source: {best['source_name']}"]


def _personal_match_score(item: Item, source_rows: list[dict[str, Any]], profile: dict[str, dict[str, float]]) -> tuple[float, list[str]]:
    text = " ".join([item.title, item.chinese_title, item.summary, item.raw_text[:1000]]).lower()
    for term in profile.get("excluded_terms", {}):
        if term and term in text:
            return -25.0, [f"excluded term: {term}"]
    matches = [
        *_weighted_matches(loads(item.tags, []), profile.get("tags", {}), limit=3),
        *_weighted_matches(loads(item.entities, []), profile.get("entities", {}), limit=3),
        *_weighted_matches([row["source_id"] for row in source_rows], profile.get("sources", {}), limit=2),
        *_weighted_matches([row["platform"] for row in source_rows] or [item.platform], profile.get("platforms", {}), limit=2),
        *_weighted_matches([item.content_type], profile.get("content_types", {}), limit=1),
    ]
    for interest in profile.get("interests", {}):
        if interest and interest in text:
            matches.append((interest, 8.0))
    score = min(sum(weight for _value, weight in matches), 35.0)
    if not matches:
        return 0.0, []
    labels = [value for value, _weight in sorted(matches, key=lambda row: row[1], reverse=True)[:4]]
    return score, [f"matches: {', '.join(labels)}"]


def _recency_score(item: Item) -> tuple[float, list[str]]:
    age_days = _item_age_days(item)
    if age_days <= 1:
        return 10.0, ["recent: 24h"]
    if age_days <= 3:
        return 7.0, ["recent: 3d"]
    if age_days <= 7:
        return 4.0, ["recent: 7d"]
    if age_days <= 30:
        return 1.5, []
    return 0.0, []


def _quality_score(item: Item) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    if item.summary_status == SummaryStatus.ready.value:
        score += 4.0
        reasons.append("AI summary ready")
    if len(item.raw_text or "") >= 1200:
        score += 3.0
    if loads(item.tags, []) or loads(item.entities, []):
        score += 2.0
    if item.url or item.canonical_url:
        score += 1.0
    return min(score, 10.0), reasons


def _trend_breakthrough_score(db: Session, item: Item, profile: dict[str, dict[str, float]]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    source_count = len(item_sources_for_item(db, item))
    if source_count > 1:
        score += min(8.0, 3.0 + source_count * 2.0)
        reasons.append(f"{source_count} sources")
    tag_burst = _tag_burst_score(db, loads(item.tags, []), profile)
    if tag_burst:
        score += tag_burst
        reasons.append("tag momentum")
    trend_score, trend_reasons = _external_trend_score(db, item, profile)
    score += trend_score
    reasons.extend(trend_reasons)
    return min(score, 25.0), reasons[:3]


def _interaction_penalty(db: Session, item: Item) -> tuple[float, list[str]]:
    reasons = []
    penalty = 0.0
    state = item_state_flags(db, item)
    if state["hidden"]:
        return -1000.0, ["hidden"]
    if state["read"]:
        penalty -= 3.0
        reasons.append("already read")
    event_counts = dict(
        db.execute(
            select(UserItemEvent.event_type, func.count(UserItemEvent.id))
            .where(
                UserItemEvent.profile_id == RECOMMENDATION_PROFILE_ID,
                UserItemEvent.item_id == item.id,
                UserItemEvent.event_type.in_(["open", "dismiss", "less_like_this"]),
            )
            .group_by(UserItemEvent.event_type)
        ).all()
    )
    if event_counts.get("open"):
        penalty -= 2.0
        reasons.append("opened before")
    if event_counts.get("less_like_this"):
        penalty -= 12.0
        reasons.append("reduced by feedback")
    if event_counts.get("dismiss"):
        penalty -= 40.0
        reasons.append("dismissed")
    return penalty, reasons


def _item_age_days(item: Item) -> float:
    now = datetime.now(timezone.utc)
    item_time = _sort_datetime(item)
    return max((now - item_time).total_seconds() / 86400, 0)


def _tag_burst_score(db: Session, tags: list[str], profile: dict[str, dict[str, float]] | None = None) -> float:
    normalized = [str(tag).strip().lower() for tag in tags if str(tag).strip()]
    if not normalized:
        return 0.0
    cutoff = datetime.now(timezone.utc) - timedelta(days=3)
    cache = profile.get("_tag_burst_cache", {}) if profile else {}
    score = 0.0
    for tag in normalized[:5]:
        if isinstance(cache, dict) and tag in cache:
            count = int(cache[tag])
        else:
            count = db.execute(
                select(func.count(Item.id)).where(
                    Item.created_at >= cutoff,
                    Item.tags.ilike(f"%{tag}%"),
                )
            ).scalar_one()
            if isinstance(cache, dict):
                cache[tag] = int(count)
        if count >= 3:
            score = max(score, min(7.0, float(count)))
    return score


def _external_trend_score(db: Session, item: Item, profile: dict[str, dict[str, float]]) -> tuple[float, list[str]]:
    provider_weights = profile.get("trend_providers", {})
    signals = profile.get("_trend_signals")  # type: ignore[assignment]
    if not isinstance(signals, list):
        signals = _recent_external_trend_signals(db)
    item_urls = {canonicalize_url(item.url or ""), canonicalize_url(item.canonical_url or "")} - {""}
    item_text = " ".join([item.title, item.chinese_title, item.summary]).lower()
    item_terms = {str(value).strip().lower() for value in [*loads(item.tags, []), *loads(item.entities, [])] if str(value).strip()}
    best_score = 0.0
    best_provider = ""
    for signal in signals:
        provider = signal.provider.lower()
        if provider_weights and provider not in provider_weights:
            continue
        matched = False
        signal_url = canonicalize_url(signal.url or "")
        if signal_url and signal_url in item_urls:
            matched = True
        signal_title = (signal.title or "").strip().lower()
        if signal_title and (signal_title in item_text or item.title.strip().lower() in signal_title):
            matched = True
        signal_terms = {str(value).strip().lower() for value in [*loads(signal.tags_json, []), *loads(signal.entities_json, [])] if str(value).strip()}
        if item_terms and signal_terms and item_terms & signal_terms:
            matched = True
        if not matched:
            continue
        normalized = min(max(float(signal.score or 0.0), 0.0), 100.0) / 100.0
        score = 6.0 + normalized * 10.0
        if score > best_score:
            best_score = score
            best_provider = signal.provider
    if not best_score:
        return 0.0, []
    return min(best_score, 16.0), [f"{best_provider} trend"]


def _attach_recommendation_runtime_caches(db: Session, profile: dict[str, dict[str, float]]) -> None:
    profile["_trend_signals"] = _recent_external_trend_signals(db)  # type: ignore[assignment]
    profile["_tag_burst_cache"] = {}  # type: ignore[assignment]


def _recent_external_trend_signals(db: Session) -> list[ExternalTrendSignal]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    return db.execute(
        select(ExternalTrendSignal)
        .where(ExternalTrendSignal.observed_at >= cutoff)
        .order_by(ExternalTrendSignal.score.desc())
        .limit(200)
    ).scalars().all()


def _cached_recommendation_scores(db: Session, item_ids: list[str], profile_id: str = RECOMMENDATION_PROFILE_ID) -> dict[str, dict[str, Any]]:
    if not item_ids:
        return {}
    now = datetime.now(timezone.utc)
    rows = db.execute(
        select(ItemRecommendationScore)
        .where(
            ItemRecommendationScore.item_id.in_(item_ids),
            ItemRecommendationScore.profile_id == profile_id,
            ItemRecommendationScore.rank_mode == "for_you",
            ItemRecommendationScore.expires_at >= now,
        )
    ).scalars().all()
    return {
        row.item_id: {
            "score": row.score,
            "components": loads(row.components_json, {}),
            "reasons": loads(row.reasons_json, []),
        }
        for row in rows
    }


def score_recommendations(db: Session, profile_id: str = RECOMMENDATION_PROFILE_ID, limit: int = 600) -> int:
    run = RecommendationRun(job_type="score_recommendations", profile_id=profile_id, status="running", model_version=RECOMMENDATION_MODEL_VERSION)
    db.add(run)
    db.commit()
    try:
        items = _recommendation_candidates(db, limit=limit)
        profile = recommendation_profile_weights(db, profile_id)
        _attach_recommendation_runtime_caches(db, profile)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=RECOMMENDATION_SCORE_TTL_MINUTES)
        for item in items:
            score, reasons, components = recommendation_for_item(db, item, profile, mode="for_you")
            row = db.execute(
                select(ItemRecommendationScore).where(
                    ItemRecommendationScore.item_id == item.id,
                    ItemRecommendationScore.profile_id == profile_id,
                    ItemRecommendationScore.rank_mode == "for_you",
                )
            ).scalar_one_or_none()
            if not row:
                row = ItemRecommendationScore(item_id=item.id, profile_id=profile_id, rank_mode="for_you")
                db.add(row)
            row.score = score
            row.components_json = dumps(components)
            row.reasons_json = dumps(reasons)
            row.model_version = RECOMMENDATION_MODEL_VERSION
            row.computed_at = utcnow()
            row.expires_at = expires_at
        run.status = "succeeded"
        run.item_count = len(items)
        run.finished_at = utcnow()
        db.commit()
        return len(items)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        run = db.get(RecommendationRun, run.id)
        if run:
            run.status = "failed"
            run.error_message = str(exc)[-4000:]
            run.finished_at = utcnow()
            db.commit()
        raise


def build_recommendation_profile(db: Session, profile_id: str = RECOMMENDATION_PROFILE_ID) -> dict[str, Any]:
    row = _ensure_user_preference(db, profile_id)
    event_profile = _event_preference_profile(db, profile_id)
    implicit = loads(row.implicit_json, {})
    for bucket, weights in event_profile.items():
        if bucket in {"excluded_terms", "trend_providers", "weights"}:
            continue
        implicit.setdefault(bucket, {})
        for key, value in weights.items():
            implicit[bucket][key] = round(value, 3)
    row.implicit_json = dumps(_clamp_profile_weights(implicit))
    db.commit()
    db.refresh(row)
    return get_recommendation_profile(db, profile_id)


def embed_item(db: Session, item_id: str) -> bool:
    item = db.get(Item, item_id)
    if not item:
        return False
    text = recommendation_embedding_text(db, item)
    input_hash = stable_hash(RECOMMENDATION_EMBEDDING_MODEL, text)
    existing = db.get(ItemEmbedding, item_id)
    if existing and existing.input_hash == input_hash:
        return False
    vector = _hash_embedding(text)
    row = existing or ItemEmbedding(item_id=item_id)
    row.model = RECOMMENDATION_EMBEDDING_MODEL
    row.input_hash = input_hash
    row.vector_json = dumps(vector)
    row.text = text[:8000]
    db.add(row)
    _expire_recommendation_scores(db, item_id=item_id)
    db.commit()
    return True


def recommendation_embedding_text(db: Session, item: Item) -> str:
    source_rows = _item_source_priority_rows(db, item)
    source_text = " ".join(f"{row['source_name']} {row['platform']}" for row in source_rows)
    return "\n".join(
        [
            item.title or "",
            item.chinese_title or "",
            item.summary or "",
            item.raw_text[:2000] or "",
            " ".join(loads(item.tags, [])),
            " ".join(loads(item.entities, [])),
            source_text,
            item.content_type or "",
            item.platform or "",
        ]
    ).strip()


def _hash_embedding(text: str, dimensions: int = 64) -> list[float]:
    vector = [0.0] * dimensions
    for token in re.findall(r"[\w.+#-]+", text.lower()):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:2], "big") % dimensions
        sign = 1.0 if digest[2] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [round(value / norm, 6) for value in vector]


def refresh_external_trends(db: Session) -> int:
    run = RecommendationRun(job_type="refresh_external_trends", profile_id=RECOMMENDATION_PROFILE_ID, status="running", model_version=RECOMMENDATION_MODEL_VERSION)
    db.add(run)
    db.commit()
    imported = 0
    errors: list[str] = []
    try:
        imported += _refresh_hn_trends(db)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"hn: {type(exc).__name__} {exc}")
    run.status = "succeeded" if not errors else "failed" if imported == 0 else "succeeded"
    run.error_message = "\n".join(errors)[-4000:]
    run.item_count = imported
    run.finished_at = utcnow()
    if imported:
        _expire_recommendation_scores(db, profile_id=RECOMMENDATION_PROFILE_ID)
    db.commit()
    if errors and imported == 0:
        raise RuntimeError(run.error_message or "External trend refresh failed")
    return imported


def _refresh_hn_trends(db: Session) -> int:
    url = "https://hn.algolia.com/api/v1/search_by_date?tags=story&hitsPerPage=40&query=AI"
    with httpx.Client(timeout=12.0, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        payload = response.json()
    imported = 0
    for hit in payload.get("hits", []):
        title = str(hit.get("title") or hit.get("story_title") or "").strip()
        link = str(hit.get("url") or hit.get("story_url") or "").strip()
        object_id = str(hit.get("objectID") or stable_hash(title, link))
        if not title:
            continue
        points = float(hit.get("points") or 0)
        comments = float(hit.get("num_comments") or 0)
        score = min(100.0, points + comments * 1.5)
        upsert_external_trend_signal(
            db,
            provider="hn",
            signal_key=object_id,
            title=title,
            url=link,
            score=score,
            tags=["ai", "hn"],
            entities=extract_entities(title),
            metadata={"points": points, "comments": comments},
            expire_scores=False,
        )
        imported += 1
    return imported


def upsert_external_trend_signal(
    db: Session,
    *,
    provider: str,
    signal_key: str,
    title: str = "",
    url: str = "",
    score: float = 0.0,
    tags: list[str] | None = None,
    entities: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    expire_scores: bool = True,
) -> ExternalTrendSignal:
    row = db.execute(
        select(ExternalTrendSignal).where(
            ExternalTrendSignal.provider == provider,
            ExternalTrendSignal.signal_key == signal_key,
        )
    ).scalar_one_or_none()
    if not row:
        row = ExternalTrendSignal(provider=provider, signal_key=signal_key)
        db.add(row)
    row.title = title
    row.url = url
    row.score = float(score or 0.0)
    row.tags_json = dumps(tags or [])
    row.entities_json = dumps(entities or [])
    row.metadata_json = dumps(metadata or {})
    row.observed_at = utcnow()
    if expire_scores:
        _expire_recommendation_scores(db, profile_id=RECOMMENDATION_PROFILE_ID)
    return row


def _recommendation_candidates(db: Session, limit: int) -> list[Item]:
    subscribed_ids = subscribed_source_ids(db)
    if not subscribed_ids:
        return []
    now = datetime.now(timezone.utc)
    recent_cutoff = now - timedelta(days=30)
    extended_cutoff = now - timedelta(days=90)
    important = _item_has_priority([(0, 74)])
    stmt = (
        select(Item)
        .where(
            _item_state_filter("hidden", False),
            _item_has_source(subscribed_ids),
            or_(
                Item.published_at >= recent_cutoff,
                and_(Item.published_at.is_(None), Item.created_at >= recent_cutoff),
                and_(
                    _item_state_filter("read", False),
                    important,
                    or_(Item.published_at >= extended_cutoff, and_(Item.published_at.is_(None), Item.created_at >= extended_cutoff)),
                ),
            ),
        )
        .order_by(Item.published_at.desc().nullslast(), Item.created_at.desc(), Item.id.desc())
        .limit(limit)
    )
    return db.execute(stmt).scalars().all()


def _expire_recommendation_scores(db: Session, item_id: str | None = None, profile_id: str | None = None) -> None:
    filters = []
    if item_id:
        filters.append(ItemRecommendationScore.item_id == item_id)
    if profile_id:
        filters.append(ItemRecommendationScore.profile_id == profile_id)
    stmt = update(ItemRecommendationScore).values(expires_at=utcnow() - timedelta(seconds=1)).execution_options(synchronize_session=False)
    if filters:
        stmt = stmt.where(and_(*filters))
    db.execute(stmt)


def _dedupe_values(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in _normalize_list(values):
        cleaned = str(value).strip()
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


async def persist_entries(db: Session, source: Source, entries: list[Any], settings: Settings) -> IngestResult:
    include = loads(source.include_keywords, [])
    exclude = loads(source.exclude_keywords, [])
    default_tags = sanitize_tags(loads(source.default_tags, []))
    tagging = normalize_tagging_config(loads(source.tagging, {}))
    fulltext_config = loads(source.fulltext, {"strategy": "feed_field"})
    raw_count = 0
    item_count = 0
    fulltext_success = 0
    fulltext_attempts = 0
    mode = _fulltext_mode(fulltext_config)
    fulltext_limit = int(
        fulltext_config.get(
            "max_detail_pages_per_run",
            fulltext_config.get("max_fulltext_per_run", 20 if mode in {"detail_only", "feed_then_detail"} else 0),
        )
        or 0
    )
    min_feed_fulltext_chars = int(fulltext_config.get("min_feed_chars", fulltext_config.get("min_feed_fulltext_chars", 1200)) or 1200)
    llm_tag_attempts = 0
    touched_item_ids: set[str] = set()
    work_intents: list[WorkIntent] = []
    for entry in entries:
        text_for_filter = f"{entry.title}\n{entry.summary}\n{entry.content}"
        if not text_matches(text_for_filter, include, exclude):
            continue
        raw_count += 1
        canonical = canonical_url_for_entry(entry)
        dedupe_key = dedupe_key_for_entry(source, entry, canonical)
        item_canonical = canonical or dedupe_key
        entry_hash = stable_hash(source.id, item_canonical, entry.title)
        existing_raw = db.execute(select(RawEntry.id).where(RawEntry.source_id == source.id, RawEntry.entry_hash == entry_hash)).scalar_one_or_none()
        if not existing_raw:
            raw = RawEntry(
                source_id=source.id,
                entry_hash=entry_hash,
                title=entry.title,
                url=entry.url,
                published_at=entry.published_at,
                authors=dumps(entry.authors),
                summary=strip_html(entry.summary),
                raw_payload=dumps(entry.raw_payload),
            )
            db.add(raw)
            db.flush()
        item = db.execute(select(Item).where(Item.dedupe_key == dedupe_key)).scalar_one_or_none()
        entry_summary = strip_html(entry.summary)
        raw_text = strip_html(entry.content or entry.summary)
        provisional_tags = _tags_from_available_values(default_tags, getattr(entry, "tags", []), tagging, [])
        if not item:
            item = Item(
                source_id=source.id,
                dedupe_key=dedupe_key,
                canonical_url=item_canonical,
                title=entry.title,
                chinese_title="",
                url=entry.url,
                content_type=source.content_type,
                platform=source.platform,
                source_name=source.name,
                authors=dumps(entry.authors),
                published_at=entry.published_at,
                summary=entry_summary,
                raw_text=raw_text,
                tags=dumps(provisional_tags),
                entities=dumps(extract_entities(f"{entry.title}\n{entry.summary}")),
                summary_status=SummaryStatus.not_configured.value if not settings.llm_configured else SummaryStatus.skipped.value,
            )
            db.add(item)
            db.flush()
            item_count += 1
        else:
            item.title = _prefer_text(item.title, entry.title)
            item.summary = _prefer_text(item.summary, entry_summary)
            item.raw_text = _prefer_text(item.raw_text, raw_text)
            item.published_at = item.published_at or entry.published_at
            item.authors = dumps(_merge_list_values(loads(item.authors, []), entry.authors))
            item.tags = dumps(_merge_list_values(loads(item.tags, []), provisional_tags))
            item.entities = dumps(_merge_list_values(loads(item.entities, []), extract_entities(f"{entry.title}\n{entry.summary}")))
            if not item.url and entry.url:
                item.url = entry.url
            if not item.canonical_url and item_canonical:
                item.canonical_url = item_canonical
        existing_source_tags = _existing_item_source_tags(db, item, source)
        item_source = upsert_item_source(db, item, source, entry.url, item_canonical, provisional_tags)
        db.flush()
        should_fetch_detail = mode == "detail_only" or (
            mode == "feed_then_detail"
            and item.url
            and _feed_text_needs_detail(raw_text, entry_summary, min_feed_fulltext_chars)
        )
        if should_fetch_detail and item.url:
            existing_fulltext = db.execute(
                select(Fulltext)
                .where(Fulltext.item_id == item.id, Fulltext.extractor == "generic_article", Fulltext.status == "succeeded")
                .limit(1)
            ).scalar_one_or_none()
            if existing_fulltext:
                if existing_fulltext.text:
                    item.raw_text = existing_fulltext.text
                fulltext_success += 1
            elif not fulltext_limit or fulltext_attempts < fulltext_limit:
                fulltext_attempts += 1
                item_id = item.id
                item_url = item.url
                db.commit()
                text, error = await extract_generic_article(item_url)
                item = db.get(Item, item_id)
                if not item:
                    continue
                db.add(Fulltext(item_id=item.id, extractor="generic_article", status="failed" if error else "succeeded", text=text, error_message=error))
                if text:
                    item.raw_text = text
                    fulltext_success += 1
        elif raw_text:
            existing_fulltext = db.execute(
                select(Fulltext.id).where(Fulltext.item_id == item.id, Fulltext.extractor == "feed_field", Fulltext.status == "succeeded").limit(1)
            ).scalar_one_or_none()
            if existing_fulltext:
                fulltext_success += 1
            else:
                db.add(Fulltext(item_id=item.id, extractor="feed_field", status="succeeded", text=raw_text))
                fulltext_success += 1
        allow_llm_tagging = _should_attempt_llm_tagging(tagging, settings, existing_source_tags, default_tags, llm_tag_attempts)
        final_tag_result = await _tags_for_entry(
            db,
            source,
            item,
            entry,
            settings,
            default_tags,
            tagging,
            allow_llm=allow_llm_tagging,
        )
        if final_tag_result.attempted:
            llm_tag_attempts += 1
        item_source = db.get(ItemSource, item_source.id) if item_source.id else item_source
        if item_source:
            item_source.tags = dumps(
                _tags_to_store_for_item_source(existing_source_tags, default_tags, tagging, final_tag_result)
            )
        db.flush()
        item.tags = dumps(_merged_item_source_tags(db, item.id) if item.id else final_tag_result.tags)
        if item.id:
            touched_item_ids.add(item.id)
        if _prepare_auto_summary_item(db, source, item, settings):
            work_intents.append(WorkIntent("summarize_item", {"item_id": item.id}))
    db.commit()
    for intent in work_intents:
        if intent.kind == "summarize_item":
            enqueue_summarize_item(db, intent.payload["item_id"])
    for item_id in touched_item_ids:
        work_intents.append(WorkIntent("embed_item", {"item_id": item_id}))
        enqueue_embed_item(db, item_id)
    return IngestResult(raw_count, item_count, fulltext_success, touched_item_ids, work_intents)


def _tags_from_available_values(default_tags: list[str], entry_tags: list[str], tagging: dict[str, Any], generated_tags: list[str]) -> list[str]:
    mode = str(tagging.get("mode") or "llm")
    max_tags = int(tagging.get("max_tags") or 5)
    if mode == "feed":
        return merge_tags(default_tags, entry_tags, max_tags=max_tags)
    if mode == "default":
        return sanitize_tags(default_tags, max_tags=max_tags)
    return merge_tags(default_tags, generated_tags, max_tags=max_tags)


def _existing_item_source_tags(db: Session, item: Item, source: Source) -> list[str]:
    if not item.id:
        return []
    raw_tags = db.execute(
        select(ItemSource.tags).where(ItemSource.item_id == item.id, ItemSource.source_id == source.id)
    ).scalar_one_or_none()
    return sanitize_tags(loads(raw_tags, []) if raw_tags else [])


def _merged_item_source_tags(db: Session, item_id: str) -> list[str]:
    tag_rows = db.execute(select(ItemSource.tags).where(ItemSource.item_id == item_id)).scalars()
    return _merge_list_values(*[loads(tags, []) for tags in tag_rows])


def _has_non_default_tags(tags: list[str], default_tags: list[str]) -> bool:
    default_set = set(sanitize_tags(default_tags))
    return any(tag not in default_set for tag in sanitize_tags(tags))


def _should_attempt_llm_tagging(
    tagging: dict[str, Any],
    settings: Settings,
    existing_source_tags: list[str],
    default_tags: list[str],
    llm_tag_attempts: int,
) -> bool:
    if str(tagging.get("mode") or "llm") != "llm":
        return False
    if not settings.llm_configured:
        return False
    if llm_tag_attempts >= LLM_TAG_MAX_PER_FETCH:
        return False
    return not _has_non_default_tags(existing_source_tags, default_tags)


def _tags_to_store_for_item_source(
    existing_source_tags: list[str],
    default_tags: list[str],
    tagging: dict[str, Any],
    result: TaggingResult,
) -> list[str]:
    max_tags = int(tagging.get("max_tags") or 5)
    mode = str(tagging.get("mode") or "llm")
    if mode != "llm" or result.generated or not _has_non_default_tags(existing_source_tags, default_tags):
        return result.tags
    return merge_tags(default_tags, existing_source_tags, max_tags=max_tags)


async def _tags_for_entry(
    db: Session,
    source: Source,
    item: Item,
    entry: Any,
    settings: Settings,
    default_tags: list[str],
    tagging: dict[str, Any],
    *,
    allow_llm: bool,
) -> TaggingResult:
    mode = str(tagging.get("mode") or "llm")
    if mode != "llm":
        return TaggingResult(_tags_from_available_values(default_tags, getattr(entry, "tags", []), tagging, []), generated=True)
    generated_tags: list[str] = []
    attempted = False
    if allow_llm:
        max_tags = int(tagging.get("max_tags") or 5)
        generated_tags = await _generate_item_tags(db, item, settings, max_tags)
        attempted = True
    return TaggingResult(
        _tags_from_available_values(default_tags, [], tagging, generated_tags),
        generated=bool(generated_tags),
        attempted=attempted,
    )


async def _generate_item_tags(db: Session, item: Item, settings: Settings, max_tags: int) -> list[str]:
    try:
        if settings.llm_provider_type == "openai_compatible":
            providers = openai_summary_provider_chain(db, settings)
            if not providers and settings.llm_configured:
                providers = [(None, settings)]
            for provider, provider_settings in providers:
                try:
                    result = await generate_tags_openai_compatible(item, provider_settings, max_tags)
                    _record_llm_usage_event(db, item, "tag_generation", "openai_compatible", provider_settings.llm_model_name or "", SummaryStatus.ready.value, result)
                    if provider:
                        provider.last_error = ""
                    return sanitize_tags(result.get("tags", []), max_tags=max_tags)
                except Exception as exc:  # noqa: BLE001
                    _record_llm_usage_event(
                        db,
                        item,
                        "tag_generation",
                        "openai_compatible",
                        provider_settings.llm_model_name or "",
                        SummaryStatus.failed.value,
                        {},
                        error_message=str(exc),
                    )
                    if provider:
                        provider.last_error = str(exc)[-1000:]
                        db.flush()
            return []
        if settings.llm_provider_type == "codex_cli":
            try:
                result = await generate_tags_codex_cli(item, settings, max_tags)
                _record_llm_usage_event(db, item, "tag_generation", "codex_cli", settings.codex_cli_model or "", SummaryStatus.ready.value, result)
                return sanitize_tags(result.get("tags", []), max_tags=max_tags)
            except Exception as exc:  # noqa: BLE001
                _record_llm_usage_event(
                    db,
                    item,
                    "tag_generation",
                    "codex_cli",
                    settings.codex_cli_model or "",
                    SummaryStatus.failed.value,
                    {},
                    error_message=str(exc),
                )
    except Exception:  # noqa: BLE001
        return []
    return []


def _record_llm_usage_event(
    db: Session,
    item: Item,
    purpose: str,
    provider: str,
    model: str,
    status: str,
    result: dict[str, Any],
    error_message: str = "",
) -> None:
    usage = result.get("usage", {}) if isinstance(result, dict) else {}
    raw_usage = usage.get("raw", {}) if isinstance(usage, dict) else {}
    event = LLMUsageEvent(
        purpose=purpose,
        item_id=item.id,
        provider=provider,
        model=model,
        status=status,
        error_message=error_message[-1000:] if error_message else "",
        prompt_tokens=int(usage.get("prompt_tokens", 0) or 0) if isinstance(usage, dict) else 0,
        completion_tokens=int(usage.get("completion_tokens", 0) or 0) if isinstance(usage, dict) else 0,
        total_tokens=int(usage.get("total_tokens", 0) or 0) if isinstance(usage, dict) else 0,
        reasoning_tokens=int(usage.get("reasoning_tokens", 0) or 0) if isinstance(usage, dict) else 0,
        usage_json=dumps(raw_usage),
        duration_ms=int(result.get("duration_ms", 0) or 0) if isinstance(result, dict) else 0,
    )
    db.add(event)
    db.flush()


def dedupe_key_for_entry(source: Source, entry: Any, canonical_url: str) -> str:
    raw_payload = entry.raw_payload if isinstance(entry.raw_payload, dict) else {}
    candidate_values = [canonical_url, entry.url, str(raw_payload.get("id") or raw_payload.get("guid") or "")]
    for link in raw_payload.get("links", []) if isinstance(raw_payload.get("links"), list) else []:
        if isinstance(link, dict):
            candidate_values.append(str(link.get("href") or ""))
    return dedupe_key_from_parts(canonical_url, entry.title, entry.published_at, source.platform or source.id, *candidate_values)


def canonical_url_for_entry(entry: Any) -> str:
    raw_payload = entry.raw_payload if isinstance(entry.raw_payload, dict) else {}
    links = raw_payload.get("links", [])
    if isinstance(links, list):
        for rel in ["canonical", "alternate"]:
            for link in links:
                if isinstance(link, dict) and str(link.get("rel") or "").lower() == rel:
                    canonical = canonicalize_url(str(link.get("href") or ""))
                    if canonical:
                        return canonical
    return canonicalize_url(entry.url)


def upsert_item_source(db: Session, item: Item, source: Source, url: str, canonical_url: str, tags: list[str]) -> ItemSource:
    row = db.execute(
        select(ItemSource).where(ItemSource.item_id == item.id, ItemSource.source_id == source.id)
    ).scalar_one_or_none()
    if not row:
        row = ItemSource(item_id=item.id, source_id=source.id)
        db.add(row)
    row.source_name = source.name
    row.url = url or row.url
    row.canonical_url = canonical_url or row.canonical_url
    row.tags = dumps(_merge_list_values(loads(row.tags, []), tags))
    row.last_seen_at = utcnow()
    return row


def _prefer_text(current: str, candidate: str) -> str:
    if not candidate:
        return current
    if not current:
        return candidate
    return candidate if len(candidate.strip()) > len(current.strip()) else current


def _merge_list_values(*values: list[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for value_list in values:
        for value in value_list or []:
            value = str(value).strip()
            if not value or value in seen:
                continue
            seen.add(value)
            merged.append(value)
    return merged


def _feed_text_needs_detail(raw_text: str, summary: str, min_chars: int) -> bool:
    raw_len = len(raw_text.strip())
    summary_len = len(summary.strip())
    if raw_len == 0:
        return True
    if raw_len < min_chars:
        return True
    return summary_len > 0 and raw_len <= summary_len * 2


def _fulltext_mode(config: dict[str, Any]) -> str:
    if config.get("mode"):
        return str(config.get("mode"))
    strategy = config.get("strategy", "feed_field")
    return {
        "feed_field": "feed_only",
        "generic_article": "detail_only",
        "feed_or_detail": "feed_then_detail",
    }.get(str(strategy), "feed_only")
