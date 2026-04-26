from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import Select, and_, delete, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.catalog import ARXIV_CS_AI_API_URL, ARXIV_CS_SE_API_URL, DEFAULT_SOURCE_PACK_PATH
from app.config import Settings
from app.fulltext import extract_generic_article, strip_html
from app.config import get_settings
from app.models import Fulltext, Item, Job, JobStatus, RawEntry, Setting, Source, SourceAttempt, SourceRun, Summary, SummaryStatus, utcnow
from app.schemas import ItemOut, SourceAttemptIn, SourceAttemptOut, SourceIn, SourceOut, SourcePatch
from app.utils import canonicalize_url, dumps, extract_entities, loads, stable_hash, text_matches


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
    )


def load_runtime_settings(db: Session) -> Settings:
    config_file_only_keys = {"rsshub_public_instances", "rsshub_self_hosted_base_url"}
    overrides: dict[str, Any] = {}
    for row in db.execute(select(Setting)).scalars():
        if row.key in config_file_only_keys:
            continue
        value = loads(row.value, None)
        overrides[row.key] = value
    return get_settings().model_copy(update={k: v for k, v in overrides.items() if v is not None})


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
    fulltext_config = loads(source.fulltext, {"strategy": "feed_field"})
    if latest_run and latest_run.status == "failed":
        status = "fetch_failed"
    elif not latest_run and not stats:
        status = "fetch_failed"
    elif source.content_type == "paper":
        status = "paper_abstract_only"
    elif stats:
        strategy = fulltext_config.get("strategy", "feed_field")
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
            and strategy == "feed_field"
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
    elif fulltext_config.get("strategy") in {"generic_article", "feed_or_detail"}:
        status = "detail_fulltext"
    else:
        status = "feed_summary_only"
    return {
        "status": status,
        "strategy": fulltext_config.get("strategy", "feed_field"),
        "min_feed_fulltext_chars": fulltext_config.get("min_feed_fulltext_chars"),
        "max_fulltext_per_run": fulltext_config.get("max_fulltext_per_run"),
    }


def sync_default_source_pack(db: Session) -> None:
    cleanup_retired_sources(db, load_retired_source_ids(DEFAULT_SOURCE_PACK_PATH))
    sync_source_pack(db, load_source_pack(DEFAULT_SOURCE_PACK_PATH), builtin=True)
    db.commit()


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
        item_ids = list(db.execute(select(Item.id).where(Item.source_id == source_id)).scalars())
        jobs = db.execute(select(Job)).scalars().all()
        for job in jobs:
            payload = loads(job.payload, {})
            if payload.get("source_id") == source_id or payload.get("item_id") in item_ids:
                db.delete(job)
        if item_ids:
            db.execute(delete(Summary).where(Summary.item_id.in_(item_ids)))
            db.execute(delete(Fulltext).where(Fulltext.item_id.in_(item_ids)))
        db.execute(delete(RawEntry).where(RawEntry.source_id == source_id))
        db.execute(delete(SourceRun).where(SourceRun.source_id == source_id))
        db.execute(delete(SourceAttempt).where(SourceAttempt.source_id == source_id))
        db.execute(delete(Item).where(Item.source_id == source_id))
        db.delete(source)
    db.flush()


def sync_known_builtin_source(db: Session, source: Source, builtin: SourceIn) -> None:
    if not source.is_builtin:
        return
    if source.id == "openai-news" and _has_legacy_openai_news_attempt(source):
        source.attempts.clear()
        db.flush()
        source.attempts = [attempt_model(attempt) for attempt in builtin.attempts]
        source.fulltext = dumps(builtin.fulltext)
        source.homepage_url = builtin.homepage_url
        source.stability_level = builtin.stability_level
    if source.id == "anthropic-news" and _has_legacy_anthropic_news_attempt(source):
        source.attempts.clear()
        db.flush()
        source.attempts = [attempt_model(attempt) for attempt in builtin.attempts]
        source.homepage_url = builtin.homepage_url
        source.stability_level = builtin.stability_level
    if source.id == "arxiv-cs-se" and _has_legacy_arxiv_cs_se_attempt(source):
        source.attempts.clear()
        db.flush()
        source.attempts = [attempt_model(attempt) for attempt in builtin.attempts]
        source.fulltext = dumps(builtin.fulltext)
        source.homepage_url = builtin.homepage_url
        source.stability_level = builtin.stability_level
    if source.id == "arxiv-cs-ai" and _has_legacy_arxiv_cs_ai_attempt(source):
        source.attempts.clear()
        db.flush()
        source.attempts = [attempt_model(attempt) for attempt in builtin.attempts]
        source.fulltext = dumps(builtin.fulltext)
        source.homepage_url = builtin.homepage_url
        source.stability_level = builtin.stability_level
    current_fulltext = loads(source.fulltext, {})
    if _should_sync_builtin_fulltext(source.id, current_fulltext):
        source.fulltext = dumps(builtin.fulltext)
    if source.id in {"anthropic-news", "anthropic-research", "anthropic-engineering"}:
        _sync_builtin_attempt_config(source, builtin)


def _has_legacy_anthropic_news_attempt(source: Source) -> bool:
    return any(attempt.adapter == "feed" and attempt.url == "https://www.anthropic.com/news/rss.xml" for attempt in source.attempts)


def _has_legacy_openai_news_attempt(source: Source) -> bool:
    return any(attempt.adapter == "feed" and attempt.url == "https://openai.com/news/rss.xml" for attempt in source.attempts)


def _has_legacy_arxiv_cs_se_attempt(source: Source) -> bool:
    return any(
        attempt.adapter == "feed"
        and attempt.url == "https://rss.arxiv.org/rss/cs.SE"
        and attempt.url != ARXIV_CS_SE_API_URL
        for attempt in source.attempts
    )


def _has_legacy_arxiv_cs_ai_attempt(source: Source) -> bool:
    return any(
        attempt.adapter == "feed"
        and attempt.url == "https://rss.arxiv.org/rss/cs.AI"
        and attempt.url != ARXIV_CS_AI_API_URL
        for attempt in source.attempts
    )


def _should_sync_builtin_fulltext(source_id: str, current: dict[str, Any]) -> bool:
    if source_id in {"openai-research", "anthropic-news", "anthropic-research", "anthropic-engineering", "huggingface-blog"}:
        return current.get("strategy") in {None, "feed_field"}
    if source_id == "openai-news":
        return current.get("strategy") == "generic_article" and "max_fulltext_per_run" not in current
    return False


def _sync_builtin_attempt_config(source: Source, builtin: SourceIn) -> None:
    configs = {(attempt.adapter, attempt.route or attempt.url): attempt.config for attempt in builtin.attempts}
    for attempt in source.attempts:
        key = (attempt.adapter, attempt.route or attempt.url)
        if key in configs and loads(attempt.config, {}) in ({}, {"timeout": 45}):
            attempt.config = dumps(configs[key])


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
    item_rows = db.execute(
        select(
            Item.source_id,
            func.count(Item.id),
            func.avg(func.length(Item.summary)),
            func.avg(func.length(Item.raw_text)),
            func.min(func.length(Item.raw_text)),
            func.max(func.length(Item.raw_text)),
        ).group_by(Item.source_id)
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
        select(Item.source_id, Fulltext.extractor, func.count(Fulltext.id))
        .join(Item, Item.id == Fulltext.item_id)
        .where(Fulltext.status == "succeeded")
        .group_by(Item.source_id, Fulltext.extractor)
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
    rows = db.execute(select(Item.source_id, Item.summary_status, func.count(Item.id)).group_by(Item.source_id, Item.summary_status)).all()
    stats: dict[str, dict[str, int]] = {}
    for source_id, status, count in rows:
        bucket = stats.setdefault(source_id, {"ready": 0, "failed": 0, "pending": 0, "not_configured": 0, "skipped": 0})
        bucket[str(status)] = int(count or 0)
    return stats


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
    source_stmt = select(Source).where(Source.auto_summary_enabled.is_(True))
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
                Item.source_id == source.id,
                Item.summary_status.in_(AUTO_SUMMARY_QUEUE_STATUSES),
                Item.raw_text != "",
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
    item_ids = set(db.execute(select(Item.id).where(Item.source_id == source_id)).scalars())
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
            Item.source_id == source.id,
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
            item.summary_status = SummaryStatus.skipped.value
            changed += 1
    if changed:
        db.commit()
    return changed


def query_items(
    db: Session,
    content_type: str | None = None,
    source_id: list[str] | None = None,
    source_group: str | None = None,
    platform: str | None = None,
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
    if content_type:
        filters.append(Item.content_type == content_type)
    if source_id:
        filters.append(Item.source_id.in_(source_id))
    if platform:
        filters.append(Item.platform == platform)
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
            filters.append(Item.published_at >= datetime.now(timezone.utc) - timedelta(days=days))
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
            )
        )
    if source_group:
        stmt = stmt.join(Source, Source.id == Item.source_id)
        filters.append(Source.group == source_group)
    if filters:
        stmt = stmt.where(and_(*filters))
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.execute(count_stmt).scalar_one()
    rows = db.execute(stmt.order_by(Item.published_at.desc().nullslast(), Item.created_at.desc()).offset(offset).limit(limit)).scalars().all()
    return rows, total


async def persist_entries(db: Session, source: Source, entries: list[Any], settings: Settings) -> tuple[int, int, int]:
    include = loads(source.include_keywords, [])
    exclude = loads(source.exclude_keywords, [])
    default_tags = loads(source.default_tags, [])
    fulltext_config = loads(source.fulltext, {"strategy": "feed_field"})
    raw_count = 0
    item_count = 0
    fulltext_success = 0
    fulltext_attempts = 0
    strategy = fulltext_config.get("strategy", "feed_field")
    fulltext_limit = int(fulltext_config.get("max_fulltext_per_run", 20 if strategy in {"generic_article", "feed_or_detail"} else 0) or 0)
    min_feed_fulltext_chars = int(fulltext_config.get("min_feed_fulltext_chars", 1200) or 1200)
    for entry in entries:
        text_for_filter = f"{entry.title}\n{entry.summary}\n{entry.content}"
        if not text_matches(text_for_filter, include, exclude):
            continue
        raw_count += 1
        canonical = canonicalize_url(entry.url) or stable_hash(source.id, entry.title)
        entry_hash = stable_hash(source.id, canonical, entry.title)
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
        item = db.execute(select(Item).where(Item.source_id == source.id, Item.canonical_url == canonical)).scalar_one_or_none()
        entry_summary = strip_html(entry.summary)
        raw_text = strip_html(entry.content or entry.summary)
        if not item:
            item = Item(
                source_id=source.id,
                canonical_url=canonical,
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
            item.title = entry.title or item.title
            item.summary = entry_summary or item.summary
            item.raw_text = raw_text or item.raw_text
            item.published_at = entry.published_at or item.published_at
        should_fetch_detail = strategy == "generic_article" or (
            strategy == "feed_or_detail"
            and source.content_type != "paper"
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
            text, error = await extract_generic_article(item.url)
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


def _feed_text_needs_detail(raw_text: str, summary: str, min_chars: int) -> bool:
    raw_len = len(raw_text.strip())
    summary_len = len(summary.strip())
    if raw_len == 0:
        return True
    if raw_len < min_chars:
        return True
    return summary_len > 0 and raw_len <= summary_len * 2


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
