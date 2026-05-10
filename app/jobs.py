import asyncio
from datetime import datetime, timedelta, timezone
from time import perf_counter

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session, selectinload

from app.adapters import AdapterError, run_attempt
from app.config import Settings, get_settings
from app.context import DEFAULT_PROFILE_ID
from app.db import SessionLocal
from app.job_queue import enqueue_build_recommendation_profile, enqueue_embed_item, enqueue_fetch_source, enqueue_refresh_trends, enqueue_score_recommendations
from app.models import Item, ItemSource, Job, JobStatus, Setting, Source, SourceRun, SourceRuntime, SourceSubscription, Summary, SummaryStatus, utcnow
from app.subscriptions import get_subscription
from app.services.ingestion import persist_entries
from app.services.core import queue_auto_summaries, reconcile_auto_summary_statuses, set_setting_value
from app.services.llm_providers import load_runtime_settings, openai_summary_provider_chain
from app.services.recommendations import (
    build_recommendation_profile,
    embed_item,
    refresh_external_trends,
    score_recommendations,
)
from app.summary import content_hash, summarize_item, summarize_openai_compatible
from app.utils import dumps, loads


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def fetch_source_job(db: Session, source_id: str, settings: Settings) -> SourceRun:
    source = db.execute(select(Source).options(selectinload(Source.attempts)).where(Source.id == source_id)).scalar_one()
    subscription = get_subscription(db, source.id)
    run = SourceRun(source_id=source.id, status="running")
    runtime = db.get(SourceRuntime, source.id)
    if not runtime:
        runtime = SourceRuntime(source_id=source.id)
        db.add(runtime)
    runtime.last_run_at = utcnow()
    db.add(run)
    db.commit()
    errors: list[str] = []
    if not subscription or not subscription.subscribed:
        run.status = "skipped"
        run.error_code = "not_subscribed"
        run.error_message = "Source is not subscribed."
        run.finished_at = utcnow()
        runtime.last_error = run.error_message
        db.commit()
        return run
    attempts = [a for a in source.attempts if a.enabled]
    if not attempts:
        run.status = "failed"
        run.error_code = "no_attempts_enabled"
        run.error_message = "No enabled attempts for this source."
        run.finished_at = utcnow()
        runtime.failure_count += 1
        runtime.last_error = run.error_message
        db.commit()
        return run
    for attempt in attempts:
        try:
            queued_before = _queued_summary_item_ids(db, source.id)
            result = await run_attempt(attempt, settings)
            ingest_result = await persist_entries(db, source, result.entries, settings)
            queue_auto_summaries(db, settings, source_id=source.id, limit=20)
            queued_after = _queued_summary_item_ids(db, source.id)
            run.status = "succeeded" if ingest_result.raw_count else "empty"
            run.raw_count = ingest_result.raw_count
            run.item_count = ingest_result.item_count
            run.fulltext_success_count = ingest_result.fulltext_success_count
            run.summary_queued_count = len(queued_after - queued_before)
            run.used_attempt_id = attempt.id
            run.used_rsshub_instance = result.used_rsshub_instance or ""
            run.finished_at = utcnow()
            if run.status == "succeeded":
                runtime.last_success_at = run.finished_at
                runtime.failure_count = 0
                runtime.empty_count = 0
                runtime.last_error = ""
            else:
                runtime.empty_count += 1
                runtime.last_error = "No entries matched this source's filters."
            db.commit()
            return run
        except AdapterError as exc:
            errors.append(f"{attempt.adapter}: {exc.code} {exc.message}")
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            run = db.get(SourceRun, run.id)
            runtime = db.get(SourceRuntime, source.id)
            errors.append(f"{attempt.adapter}: {type(exc).__name__} {exc}")
    run.status = "failed"
    run.error_code = "all_attempts_failed"
    run.error_message = "\n".join(errors)[-4000:]
    run.finished_at = utcnow()
    runtime.failure_count += 1
    runtime.last_error = run.error_message
    db.commit()
    return run


def _queued_summary_item_ids(db: Session, source_id: str) -> set[str]:
    item_ids = set(
        db.execute(
            select(ItemSource.item_id)
            .where(ItemSource.source_id == source_id)
        ).scalars()
    )
    if not item_ids:
        return set()
    jobs = db.execute(
        select(Job).where(Job.type == "summarize_item", Job.status == JobStatus.queued.value)
    ).scalars()
    queued: set[str] = set()
    for job in jobs:
        payload = loads(job.payload, {})
        item_id = payload.get("item_id")
        if isinstance(item_id, str) and item_id in item_ids:
            queued.add(item_id)
    return queued


async def summarize_item_job(db: Session, item_id: str, settings: Settings) -> None:
    item = db.get(Item, item_id)
    if not item:
        return
    if not settings.llm_configured:
        item.summary_status = SummaryStatus.not_configured.value
        db.commit()
        return
    item.summary_status = SummaryStatus.pending.value
    db.commit()
    if settings.llm_provider_type == "openai_compatible":
        await _summarize_item_with_openai_chain(db, item, settings)
        return
    started = perf_counter()
    try:
        result = await summarize_item(item, settings)
        data = result.get("data", result)
        usage = result.get("usage", {})
        raw_usage = usage.get("raw", {}) if isinstance(usage, dict) else {}
        duration_ms = int(result.get("duration_ms") or ((perf_counter() - started) * 1000))
        summary = Summary(
            item_id=item.id,
            provider=settings.llm_provider_type,
            model=settings.llm_model_name or settings.codex_cli_model or "",
            prompt_version="v1",
            content_hash=content_hash(item),
            status=SummaryStatus.ready.value,
            data=dumps(data),
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
            total_tokens=int(usage.get("total_tokens", 0) or 0),
            reasoning_tokens=int(usage.get("reasoning_tokens", 0) or 0),
            usage_json=dumps(raw_usage),
            duration_ms=duration_ms,
        )
        item.summary_status = SummaryStatus.ready.value
        item.chinese_title = data.get("one_sentence", "")[:120]
        item.summary = data.get("one_sentence", item.summary)
        db.add(summary)
    except Exception as exc:  # noqa: BLE001
        item.summary_status = SummaryStatus.failed.value
        db.add(
            Summary(
                item_id=item.id,
                provider=settings.llm_provider_type,
                model=settings.llm_model_name or settings.codex_cli_model or "",
                prompt_version="v1",
                content_hash=content_hash(item),
                status=SummaryStatus.failed.value,
                error_message=str(exc)[-1000:],
                duration_ms=int((perf_counter() - started) * 1000),
            )
        )
    db.commit()
    enqueue_embed_item(db, item.id)


async def _summarize_item_with_openai_chain(db: Session, item: Item, settings: Settings) -> None:
    providers = openai_summary_provider_chain(db, settings)
    if not providers:
        item.summary_status = SummaryStatus.not_configured.value
        db.commit()
        return
    last_error = ""
    for provider, provider_settings in providers:
        started = perf_counter()
        try:
            result = await summarize_openai_compatible(item, provider_settings)
            data = result.get("data", result)
            usage = result.get("usage", {})
            raw_usage = usage.get("raw", {}) if isinstance(usage, dict) else {}
            duration_ms = int(result.get("duration_ms") or ((perf_counter() - started) * 1000))
            db.add(
                Summary(
                    item_id=item.id,
                    provider="openai_compatible",
                    model=provider_settings.llm_model_name or "",
                    prompt_version="v1",
                    content_hash=content_hash(item),
                    status=SummaryStatus.ready.value,
                    data=dumps(data),
                    prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                    completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                    total_tokens=int(usage.get("total_tokens", 0) or 0),
                    reasoning_tokens=int(usage.get("reasoning_tokens", 0) or 0),
                    usage_json=dumps(raw_usage),
                    duration_ms=duration_ms,
                )
            )
            if provider:
                provider.last_error = ""
            item.summary_status = SummaryStatus.ready.value
            item.chinese_title = data.get("one_sentence", "")[:120]
            item.summary = data.get("one_sentence", item.summary)
            db.commit()
            enqueue_embed_item(db, item.id)
            return
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)[-1000:]
            if provider:
                provider.last_error = last_error
            db.add(
                Summary(
                    item_id=item.id,
                    provider="openai_compatible",
                    model=provider_settings.llm_model_name or "",
                    prompt_version="v1",
                    content_hash=content_hash(item),
                    status=SummaryStatus.failed.value,
                    error_message=last_error,
                    duration_ms=int((perf_counter() - started) * 1000),
                )
            )
            db.flush()
    item.summary_status = SummaryStatus.failed.value
    db.commit()


async def run_job(db: Session, job: Job, settings: Settings) -> None:
    job_id = job.id
    if job.status != JobStatus.running.value:
        claimed = _claim_job_by_id(db, job.id)
        if not claimed:
            return
        job = claimed
    try:
        payload = loads(job.payload, {})
        if job.type == "fetch_source":
            run = await fetch_source_job(db, payload["source_id"], settings)
            if run.status == "failed":
                raise RuntimeError(run.error_message or run.error_code)
        elif job.type == "summarize_item":
            await summarize_item_job(db, payload["item_id"], settings)
        elif job.type == "embed_item":
            embed_item(db, payload["item_id"])
        elif job.type == "refresh_external_trends":
            refresh_external_trends(db)
            enqueue_build_recommendation_profile(db, payload["profile_id"])
        elif job.type == "build_recommendation_profile":
            profile_id = payload["profile_id"]
            build_recommendation_profile(db, profile_id)
            enqueue_score_recommendations(db, profile_id)
        elif job.type == "score_recommendations":
            score_recommendations(db, payload["profile_id"])
        else:
            raise RuntimeError(f"Unsupported job type: {job.type}")
        job.status = JobStatus.succeeded.value
        job.finished_at = utcnow()
        job.error_code = ""
        job.error_message = ""
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        job = db.get(Job, job_id)
        if not job:
            return
        job.error_code = type(exc).__name__
        job.error_message = str(exc)[-4000:]
        job.finished_at = utcnow()
        if job.attempts < job.max_attempts:
            job.status = JobStatus.retrying.value
            job.next_run_at = utcnow() + timedelta(seconds=min(300, 10 * (2 ** max(job.attempts - 1, 0))))
        else:
            job.status = JobStatus.failed.value
    db.commit()


def _claim_job_by_id(db: Session, job_id: int) -> Job | None:
    now = datetime.now(timezone.utc)
    result = db.execute(
        update(Job)
        .where(
            Job.id == job_id,
            Job.status.in_([JobStatus.queued.value, JobStatus.retrying.value]),
            Job.scheduled_at <= now,
            or_(Job.next_run_at.is_(None), Job.next_run_at <= now),
        )
        .values(
            status=JobStatus.running.value,
            started_at=utcnow(),
            lease_until=utcnow() + timedelta(seconds=get_settings().worker_max_job_runtime_seconds),
            attempts=Job.attempts + 1,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        return None
    db.commit()
    return db.get(Job, job_id)


def claim_next_job(db: Session) -> Job | None:
    candidate = db.execute(
        select(Job.id)
        .where(
            Job.status.in_([JobStatus.queued.value, JobStatus.retrying.value]),
            Job.scheduled_at <= datetime.now(timezone.utc),
            or_(Job.next_run_at.is_(None), Job.next_run_at <= datetime.now(timezone.utc)),
        )
        .order_by(Job.priority, Job.scheduled_at, Job.id)
        .limit(1)
    ).scalar_one_or_none()
    if candidate is None:
        return None
    return _claim_job_by_id(db, candidate)


def recover_interrupted_work(db: Session, max_age_seconds: int, force: bool = False) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
    recovered = 0
    running_jobs = db.execute(select(Job).where(Job.status == JobStatus.running.value)).scalars().all()
    for job in running_jobs:
        started_at = _aware(job.started_at)
        if not force and started_at and started_at > cutoff:
            continue
        job.status = JobStatus.retrying.value if job.attempts < job.max_attempts else JobStatus.failed.value
        job.error_code = "interrupted"
        job.error_message = "Recovered a job left running by a stopped or stuck worker."
        job.finished_at = utcnow()
        recovered += 1
    running_runs = db.execute(select(SourceRun).where(SourceRun.status == "running")).scalars().all()
    for run in running_runs:
        started_at = _aware(run.started_at)
        if not force and started_at and started_at > cutoff:
            continue
        run.status = "failed"
        run.error_code = "interrupted"
        run.error_message = "Recovered a source run left running by a stopped or stuck worker."
        run.finished_at = utcnow()
        recovered += 1
    if recovered:
        db.commit()
    return recovered


async def worker_loop() -> None:
    base_settings = get_settings()
    with SessionLocal() as db:
        recover_interrupted_work(db, base_settings.worker_max_job_runtime_seconds)
    while True:
        with SessionLocal() as db:
            recover_interrupted_work(db, base_settings.worker_max_job_runtime_seconds)
            job = claim_next_job(db)
            if job:
                await run_job(db, job, load_runtime_settings(db))
        await asyncio.sleep(base_settings.worker_sleep_seconds)


def schedule_due_sources(db: Session) -> int:
    now = datetime.now(timezone.utc)
    scheduled = 0
    stmt = (
        select(Source)
        .join(SourceSubscription, SourceSubscription.source_id == Source.id)
        .where(SourceSubscription.profile_id == DEFAULT_PROFILE_ID, SourceSubscription.subscribed.is_(True))
    )
    for source in db.execute(stmt).scalars():
        latest = db.execute(
            select(SourceRun).where(SourceRun.source_id == source.id).order_by(SourceRun.started_at.desc()).limit(1)
        ).scalar_one_or_none()
        latest_started_at = _aware(latest.started_at) if latest else None
        if latest and latest_started_at and (now - latest_started_at).total_seconds() < source.poll_interval:
            continue
        job = enqueue_fetch_source(db, source.id)
        if getattr(job, "_queue_created", False):
            scheduled += 1
    return scheduled


def schedule_auto_summaries(db: Session, settings: Settings, limit: int = 20) -> int:
    return queue_auto_summaries(db, settings, limit=limit)


def schedule_recommendation_jobs(db: Session, interval_seconds: int = 900) -> int:
    now = datetime.now(timezone.utc)
    row = db.get(Setting, "recommendation.last_scheduled_at")
    latest_value = loads(row.value, None) if row else None
    latest = _aware(datetime.fromisoformat(latest_value)) if isinstance(latest_value, str) else _aware(latest_value)
    if latest and (now - latest).total_seconds() < interval_seconds:
        return 0
    job = enqueue_refresh_trends(db, DEFAULT_PROFILE_ID)
    queued = 1 if getattr(job, "_queue_created", False) else 0
    set_setting_value(db, "recommendation.last_scheduled_at", now.isoformat())
    db.commit()
    return queued


async def scheduler_loop() -> None:
    settings = get_settings()
    while True:
        with SessionLocal() as db:
            schedule_due_sources(db)
            runtime_settings = load_runtime_settings(db)
            reconcile_auto_summary_statuses(db, runtime_settings, limit=500)
            schedule_auto_summaries(db, runtime_settings, limit=20)
            schedule_recommendation_jobs(db)
        await asyncio.sleep(settings.scheduler_sleep_seconds)
