from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import Select, and_, delete, distinct, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.catalog import DEFAULT_SOURCE_PACK_PATH
from app.config import Settings, get_settings
from app.fulltext import extract_generic_article, strip_html
from app.models import Fulltext, Item, ItemSource, Job, JobStatus, LLMProvider, RawEntry, Setting, Source, SourceAttempt, SourceRun, SourceRuntime, SourceSubscription, Summary, SummaryStatus, utcnow
from app.schemas import ItemOut, SourceAttemptIn, SourceAttemptOut, SourceDefinitionIn, SourceDefinitionOut, SourceOut, SourcePatch, SourceRuntimeOut, SourceIn
from app.source_catalog import definition_from_source, sync_source_catalog, upsert_source_definition
from app.subscriptions import subscribed_source_ids
from app.utils import canonicalize_url, dedupe_key_from_parts, dumps, extract_entities, loads, stable_hash, text_matches


def load_source_pack(path: str | Path) -> list[SourceIn]:
    payload = load_source_pack_payload(path)
    return [SourceIn.model_validate(raw) for raw in payload.get("sources", [])]


def load_source_pack_payload(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Source pack must be a mapping: {path}")
    return payload


def load_retired_source_ids(path: str | Path) -> set[str]:
    payload = load_source_pack_payload(path)
    return {str(source_id) for source_id in payload.get("retired_source_ids", [])}


def source_to_out(source: Source, latest_run: SourceRun | None = None) -> SourceOut:
    return SourceOut(
        id=source.id,
        name=source.name,
        content_type=source.content_type,
        platform=source.platform,
        homepage_url=source.homepage_url,
        enabled=source.enabled,
        is_builtin=source.is_builtin,
        group=source.group,
        priority=source.priority,
        poll_interval=source.poll_interval,
        auto_summary_enabled=source.auto_summary_enabled,
        auto_summary_days=source.auto_summary_days,
        language_hint=source.language_hint,
        include_keywords=loads(source.include_keywords, []),
        exclude_keywords=loads(source.exclude_keywords, []),
        default_tags=loads(source.default_tags, []),
        attempts=[
            SourceAttemptOut(
                id=attempt.id,
                kind=attempt.kind,
                adapter=attempt.adapter,
                url=attempt.url,
                route=attempt.route,
                priority=attempt.priority,
                enabled=attempt.enabled,
                config=loads(attempt.config, {}),
            )
            for attempt in source.attempts
        ],
        fulltext=loads(source.fulltext, {"strategy": "feed_field"}),
        auth_mode=source.auth_mode,
        stability_level=source.stability_level,
        latest_run=run_to_dict(latest_run) if latest_run else None,
        content_audit=content_audit_for_source(source, latest_run),
    )


def source_definition_to_out(source: Source, latest_run: SourceRun | None = None, stats: dict[str, Any] | None = None) -> SourceDefinitionOut:
    definition = definition_from_source(source)
    subscription = source.subscription
    runtime = source.runtime
    subscribed = bool(subscription and subscription.subscribed)
    attempts = [
        SourceAttemptIn(
            kind=attempt.kind,
            adapter=attempt.adapter,
            url=attempt.url,
            route=attempt.route,
            priority=attempt.priority,
            enabled=attempt.enabled,
            config=loads(attempt.config, {}),
        )
        for attempt in source.attempts
    ]
    return SourceDefinitionOut(
        **definition.model_dump(),
        subscribed=subscribed,
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
        content_audit=content_audit_for_source(source, latest_run, stats),
        spec_hash=source.spec_hash,
        catalog_file=source.catalog_file,
        name=definition.title,
        content_type=definition.kind,
        homepage_url=definition.homepage,
        enabled=subscribed,
        is_builtin=source.is_builtin,
        language_hint=definition.language,
        default_tags=definition.tags,
        include_keywords=definition.filters.include_keywords,
        exclude_keywords=definition.filters.exclude_keywords,
        attempts=attempts,
        auto_summary_enabled=bool(definition.summary.auto if definition.summary else False),
        auto_summary_days=int(definition.summary.window_days if definition.summary else 7),
        auth_mode=definition.auth.mode,
        stability_level=definition.stability,
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


def item_to_out(item: Item, db: Session | None = None) -> ItemOut:
    item_sources = item_sources_for_item(db, item) if db else []
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
        read=item.read,
        starred=item.starred,
        hidden=item.hidden,
        summary_status=item.summary_status,
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


def seed_builtin_sources(db: Session) -> None:
    sync_default_source_pack(db)


def sync_source_pack(db: Session, sources: list[SourceIn], builtin: bool = False) -> int:
    count = 0
    for entry in sources:
        existing = db.get(Source, entry.id)
        if existing:
            if builtin:
                sync_known_builtin_source(db, existing, entry)
            else:
                patch_source(db, existing, SourcePatch(**entry.model_dump(exclude={"id"})))
            count += 1
            continue
        source = create_source_model(entry, is_builtin=builtin)
        db.add(source)
        count += 1
    return count


def cleanup_retired_sources(db: Session, retired_ids: set[str]) -> None:
    for source_id in retired_ids:
        source = db.get(Source, source_id)
        if not source or not source.is_builtin:
            continue
        affected_item_ids = list(db.execute(select(ItemSource.item_id).where(ItemSource.source_id == source_id)).scalars())
        jobs = db.execute(select(Job)).scalars().all()
        for job in jobs:
            payload = loads(job.payload, {})
            if payload.get("source_id") == source_id:
                db.delete(job)
        db.execute(delete(ItemSource).where(ItemSource.source_id == source_id))
        orphan_item_ids: list[str] = []
        for item_id in affected_item_ids:
            item = db.get(Item, item_id)
            if not item:
                continue
            replacement = db.execute(
                select(ItemSource, Source)
                .join(Source, Source.id == ItemSource.source_id)
                .where(ItemSource.item_id == item_id)
                .order_by(ItemSource.first_seen_at, ItemSource.id)
                .limit(1)
            ).first()
            if replacement:
                item_source, replacement_source = replacement
                item.source_id = item_source.source_id
                item.source_name = item_source.source_name or replacement_source.name
                item.platform = replacement_source.platform
                item.content_type = replacement_source.content_type
                item.url = item_source.url or item.url
                item.canonical_url = item_source.canonical_url or item.canonical_url
                item.tags = dumps(_merged_item_source_tags(db, item_id))
            else:
                orphan_item_ids.append(item_id)
        for job in jobs:
            payload = loads(job.payload, {})
            if payload.get("item_id") in orphan_item_ids:
                db.delete(job)
        if orphan_item_ids:
            db.execute(delete(Summary).where(Summary.item_id.in_(orphan_item_ids)))
            db.execute(delete(Fulltext).where(Fulltext.item_id.in_(orphan_item_ids)))
            db.execute(delete(Item).where(Item.id.in_(orphan_item_ids)))
        db.flush()
        db.execute(delete(RawEntry).where(RawEntry.source_id == source_id))
        db.execute(delete(SourceRun).where(SourceRun.source_id == source_id))
        db.execute(delete(SourceAttempt).where(SourceAttempt.source_id == source_id))
        db.delete(source)
    db.flush()


def _merged_item_source_tags(db: Session, item_id: str) -> list[str]:
    tag_rows = db.execute(select(ItemSource.tags).where(ItemSource.item_id == item_id)).scalars()
    return _merge_list_values(*[loads(tags, []) for tags in tag_rows])


def sync_known_builtin_source(db: Session, source: Source, builtin: SourceIn) -> None:
    if not source.is_builtin:
        return
    return


def create_source_model(data: SourceIn, is_builtin: bool = False) -> Source:
    source = Source(
        id=data.id,
        name=data.name,
        content_type=data.content_type,
        platform=data.platform,
        homepage_url=data.homepage_url,
        enabled=data.enabled,
        is_builtin=is_builtin,
        group=data.group,
        priority=data.priority,
        poll_interval=data.poll_interval,
        auto_summary_enabled=bool(data.auto_summary_enabled),
        auto_summary_days=data.auto_summary_days,
        language_hint=data.language_hint,
        include_keywords=dumps(data.include_keywords),
        exclude_keywords=dumps(data.exclude_keywords),
        default_tags=dumps(data.default_tags),
        fulltext=dumps(data.fulltext),
        auth_mode=data.auth_mode,
        stability_level=data.stability_level,
    )
    source.attempts = [attempt_model(attempt) for attempt in data.attempts]
    return source


def attempt_model(data: SourceAttemptIn) -> SourceAttempt:
    return SourceAttempt(
        kind=data.kind,
        adapter=data.adapter,
        url=data.url,
        route=data.route,
        priority=data.priority,
        enabled=data.enabled,
        config=dumps(data.config),
    )


def patch_source(db: Session, source: Source, patch: SourcePatch) -> Source:
    for field in [
        "name",
        "content_type",
        "platform",
        "homepage_url",
        "enabled",
        "group",
        "priority",
        "poll_interval",
        "auto_summary_enabled",
        "auto_summary_days",
        "language_hint",
        "auth_mode",
        "stability_level",
    ]:
        value = getattr(patch, field)
        if value is not None:
            setattr(source, field, value)
    for field in ["include_keywords", "exclude_keywords", "default_tags", "fulltext"]:
        value = getattr(patch, field)
        if value is not None:
            setattr(source, field, dumps(value))
    if patch.attempts is not None:
        source.attempts.clear()
        db.flush()
        source.attempts = [attempt_model(attempt) for attempt in patch.attempts]
    return source


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
    return stats


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
    filters = [Summary.provider == "openai_compatible"]
    if cutoff is not None:
        filters.append(Summary.created_at >= cutoff)
    if model is not None:
        filters.append(Summary.model == model)
    status_rows = db.execute(select(Summary.status, func.count(Summary.id)).where(*filters).group_by(Summary.status)).all()
    status_counts = {str(status): int(count or 0) for status, count in status_rows}
    token_row = db.execute(
        select(
            func.coalesce(func.sum(Summary.prompt_tokens), 0),
            func.coalesce(func.sum(Summary.completion_tokens), 0),
            func.coalesce(func.sum(Summary.total_tokens), 0),
            func.coalesce(func.sum(Summary.reasoning_tokens), 0),
            func.coalesce(func.sum(Summary.duration_ms), 0),
        ).where(*filters)
    ).one()
    requests = sum(status_counts.values())
    return {
        "requests": requests,
        "success": status_counts.get(SummaryStatus.ready.value, 0),
        "failed": status_counts.get(SummaryStatus.failed.value, 0),
        "prompt_tokens": int(token_row[0] or 0),
        "completion_tokens": int(token_row[1] or 0),
        "total_tokens": int(token_row[2] or 0),
        "reasoning_tokens": int(token_row[3] or 0),
        "duration_ms": int(token_row[4] or 0),
    }


def llm_usage_stats(db: Session) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    last_used_at = db.execute(select(func.max(Summary.created_at)).where(Summary.provider == "openai_compatible")).scalar_one()
    last_error = db.execute(
        select(Summary)
        .where(Summary.provider == "openai_compatible", Summary.error_message != "")
        .order_by(Summary.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    model_rows = db.execute(
        select(Summary.model)
        .where(Summary.provider == "openai_compatible")
        .group_by(Summary.model)
        .order_by(Summary.model)
    ).all()
    return {
        "provider": "openai_compatible",
        "all_time": _summary_usage_bucket(db),
        "recent_24h": _summary_usage_bucket(db, now - timedelta(days=1)),
        "recent_7d": _summary_usage_bucket(db, now - timedelta(days=7)),
        "by_model": [
            {"model": model or "unknown", **_summary_usage_bucket(db, model=model)}
            for (model,) in model_rows
        ],
        "last_used_at": last_used_at,
        "last_error_at": last_error.created_at if last_error else None,
        "last_error": last_error.error_message if last_error else "",
    }


def list_sources(db: Session) -> list[SourceOut]:
    runs = latest_runs(db)
    stats = source_content_stats(db)
    sources = db.execute(select(Source).options(selectinload(Source.attempts)).order_by(Source.group, Source.priority)).scalars().all()
    return [
        source_to_out(source, runs.get(source.id)).model_copy(
            update={"content_audit": content_audit_for_source(source, runs.get(source.id), stats.get(source.id))}
        )
        for source in sources
    ]


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
    source = upsert_source_definition(db, definition, catalog_file="custom", builtin=False)
    if subscribe:
        db.add(SourceSubscription(source_id=source.id, subscribed=True))
    db.commit()
    db.refresh(source)
    return source_definition_to_out(source)


def queue_job(db: Session, job_type: str, payload: dict[str, Any], max_attempts: int = 3) -> Job:
    existing = db.execute(
        select(Job).where(
            Job.type == job_type,
            Job.payload == dumps(payload),
            Job.status.in_(["queued", "running", "retrying"]),
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    job = Job(type=job_type, payload=dumps(payload), max_attempts=max_attempts)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


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
        .where(Source.auto_summary_enabled.is_(True), SourceSubscription.subscribed.is_(True))
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
            queue_job(db, "summarize_item", {"item_id": item.id})
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


def query_items(
    db: Session,
    source_id: list[str] | None = None,
    source_group: str | None = None,
    platform: str | None = None,
    include_unsubscribed: bool = False,
    q: str | None = None,
    since: str | None = None,
    summary_status: str | None = None,
    read: bool | None = None,
    starred: bool | None = None,
    hidden: bool | None = False,
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
    if platform:
        filters.append(
            select(ItemSource.id)
            .join(Source, Source.id == ItemSource.source_id)
            .where(ItemSource.item_id == Item.id, Source.platform == platform)
            .exists()
        )
    if summary_status:
        filters.append(Item.summary_status == summary_status)
    if read is not None:
        filters.append(Item.read == read)
    if starred is not None:
        filters.append(Item.starred == starred)
    if hidden is not None:
        filters.append(Item.hidden == hidden)
    if since:
        days = {"today": 1, "3d": 3, "7d": 7}.get(since)
        if days:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            filters.append(or_(Item.published_at >= cutoff, and_(Item.published_at.is_(None), Item.created_at >= cutoff)))
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
    if source_group:
        filters.append(
            select(ItemSource.id)
            .join(Source, Source.id == ItemSource.source_id)
            .where(ItemSource.item_id == Item.id, Source.group == source_group)
            .exists()
        )
    if filters:
        stmt = stmt.where(and_(*filters))
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.execute(count_stmt).scalar_one()
    rows = db.execute(stmt.order_by(Item.published_at.desc().nullslast(), Item.created_at.desc()).offset(offset).limit(limit)).scalars().all()
    return rows, total


def _item_has_source(source_ids: list[str]) -> Any:
    return select(ItemSource.id).where(ItemSource.item_id == Item.id, ItemSource.source_id.in_(source_ids)).exists()


async def persist_entries(db: Session, source: Source, entries: list[Any], settings: Settings) -> tuple[int, int, int]:
    include = loads(source.include_keywords, [])
    exclude = loads(source.exclude_keywords, [])
    default_tags = loads(source.default_tags, [])
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
                tags=dumps(default_tags),
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
            item.tags = dumps(_merge_list_values(loads(item.tags, []), default_tags))
            item.entities = dumps(_merge_list_values(loads(item.entities, []), extract_entities(f"{entry.title}\n{entry.summary}")))
            if not item.url and entry.url:
                item.url = entry.url
            if not item.canonical_url and item_canonical:
                item.canonical_url = item_canonical
        upsert_item_source(db, item, source, entry.url, item_canonical, default_tags)
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
                continue
            if fulltext_limit and fulltext_attempts >= fulltext_limit:
                continue
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
        if _prepare_auto_summary_item(db, source, item, settings):
            queue_job(db, "summarize_item", {"item_id": item.id})
    db.commit()
    return raw_count, item_count, fulltext_success


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


def export_source_pack(db: Session) -> str:
    sources = list_sources(db)
    payload = {"version": 1, "sources": [source.model_dump(exclude={"latest_run", "content_audit"}) for source in sources]}
    for source in payload["sources"]:
        source.pop("is_builtin", None)
        for attempt in source["attempts"]:
            attempt.pop("id", None)
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)


def import_source_pack(db: Session, text: str) -> int:
    payload = yaml.safe_load(text) or {}
    sources = [SourceIn.model_validate(raw) for raw in payload.get("sources", [])]
    count = sync_source_pack(db, sources, builtin=False)
    db.commit()
    return count
