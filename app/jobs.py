import asyncio
from datetime import datetime, timedelta, timezone
from time import perf_counter

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.adapters import AdapterError, run_attempt
from app.config import Settings, get_settings
from app.db import SessionLocal
from app.models import Item, ItemSource, Job, JobStatus, Source, SourceRun, SourceRuntime, SourceSubscription, Summary, SummaryStatus, utcnow
from app.services import load_runtime_settings, openai_summary_provider_chain, persist_entries, queue_auto_summaries, queue_job, reconcile_auto_summary_statuses
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
    subscription = db.get(SourceSubscription, source.id)
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
            raw_count, item_count, fulltext_success = await persist_entries(db, source, result.entries, settings)
            queue_auto_summaries(db, settings, source_id=source.id, limit=20)
            queued_after = _queued_summary_item_ids(db, source.id)
            run.status = "succeeded" if raw_count else "empty"
            run.raw_count = raw_count
            run.item_count = item_count
            run.fulltext_success_count = fulltext_success
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
    job.status = JobStatus.running.value
    job.started_at = utcnow()
    job.attempts += 1
    db.commit()
    try:
        payload = loads(job.payload, {})
        if job.type == "fetch_source":
            run = await fetch_source_job(db, payload["source_id"], settings)
            if run.status == "failed":
                raise RuntimeError(run.error_message or run.error_code)
        elif job.type == "summarize_item":
            await summarize_item_job(db, payload["item_id"], settings)
        else:
            raise RuntimeError(f"Unsupported job type: {job.type}")
        job.status = JobStatus.succeeded.value
        job.finished_at = utcnow()
        job.error_code = ""
        job.error_message = ""
    except Exception as exc:  # noqa: BLE001
        job.error_code = type(exc).__name__
        job.error_message = str(exc)[-4000:]
        job.finished_at = utcnow()
        job.status = JobStatus.retrying.value if job.attempts < job.max_attempts else JobStatus.failed.value
    db.commit()


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
        recover_interrupted_work(db, base_settings.worker_max_job_runtime_seconds, force=True)
    while True:
        with SessionLocal() as db:
            recover_interrupted_work(db, base_settings.worker_max_job_runtime_seconds)
            job = db.execute(
                select(Job)
                .where(Job.status.in_([JobStatus.queued.value, JobStatus.retrying.value]), Job.scheduled_at <= datetime.now(timezone.utc))
                .order_by(Job.scheduled_at, Job.id)
                .limit(1)
            ).scalar_one_or_none()
            if job:
                await run_job(db, job, load_runtime_settings(db))
        await asyncio.sleep(base_settings.worker_sleep_seconds)


def schedule_due_sources(db: Session) -> int:
    now = datetime.now(timezone.utc)
    scheduled = 0
    stmt = (
        select(Source)
        .join(SourceSubscription, SourceSubscription.source_id == Source.id)
        .where(SourceSubscription.subscribed.is_(True))
    )
    for source in db.execute(stmt).scalars():
        latest = db.execute(
            select(SourceRun).where(SourceRun.source_id == source.id).order_by(SourceRun.started_at.desc()).limit(1)
        ).scalar_one_or_none()
        latest_started_at = _aware(latest.started_at) if latest else None
        if latest and latest_started_at and (now - latest_started_at).total_seconds() < source.poll_interval:
            continue
        queue_job(db, "fetch_source", {"source_id": source.id})
        scheduled += 1
    return scheduled


def schedule_auto_summaries(db: Session, settings: Settings, limit: int = 20) -> int:
    return queue_auto_summaries(db, settings, limit=limit)


async def scheduler_loop() -> None:
    settings = get_settings()
    while True:
        with SessionLocal() as db:
            schedule_due_sources(db)
            runtime_settings = load_runtime_settings(db)
            reconcile_auto_summary_statuses(db, runtime_settings, limit=500)
            schedule_auto_summaries(db, runtime_settings, limit=20)
        await asyncio.sleep(settings.scheduler_sleep_seconds)
