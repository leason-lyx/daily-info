from datetime import datetime, timedelta, timezone
import shutil
from typing import Annotated
from urllib.parse import urlsplit, urlunsplit

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.adapters import AdapterError, preview_source
from app.db import get_db, init_db
from app.jobs import schedule_auto_summaries, schedule_due_sources
from app.models import Item, Job, JobStatus, Setting, Source, SourceRun, Summary, SummaryStatus
from app.schemas import (
    AiProviderTestResult,
    ItemListOut,
    PreviewRequest,
    PreviewResponse,
    SettingsOut,
    SettingsPatch,
    SourceIn,
    SourceOut,
    SourcePatch,
)
from app.services import (
    content_audit_for_source,
    create_source_model,
    export_source_pack,
    import_source_pack,
    item_to_out,
    list_sources,
    load_runtime_settings,
    llm_usage_stats,
    latest_runs,
    patch_source,
    query_items,
    queue_auto_summaries,
    queue_job,
    reconcile_auto_summary_statuses,
    sync_default_source_pack,
    source_content_stats,
    source_summary_stats,
    source_to_out,
)
from app.summary import summarize_codex_cli, summarize_openai_compatible
from app.utils import dumps, loads


app = FastAPI(title="Daily Info API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    init_db()
    with next(get_db()) as db:
        sync_default_source_pack(db)
        reconcile_auto_summary_statuses(db, load_runtime_settings(db))


Db = Annotated[Session, Depends(get_db)]


@app.get("/api/items", response_model=ItemListOut)
def get_items(
    db: Db,
    content_type: str | None = None,
    source_id: list[str] | None = Query(default=None),
    source_group: str | None = None,
    platform: str | None = None,
    q: str | None = None,
    since: str | None = None,
    summary_status: str | None = None,
    read: bool | None = None,
    starred: bool | None = None,
    hidden: bool | None = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ItemListOut:
    items, total = query_items(db, content_type, source_id, source_group, platform, q, since, summary_status, read, starred, hidden, limit, offset)
    return ItemListOut(items=[item_to_out(item, db) for item in items], total=total)


@app.get("/api/items/{item_id}")
def get_item(item_id: str, db: Db):
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item_to_out(item, db)


def _set_item_flag(db: Session, item_id: str, field: str, value: bool | None):
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    current = getattr(item, field)
    setattr(item, field, (not current) if value is None else value)
    db.commit()
    return item_to_out(item, db)


@app.post("/api/items/{item_id}/read")
def mark_read(item_id: str, db: Db, value: bool | None = Body(default=None, embed=True)):
    return _set_item_flag(db, item_id, "read", value)


@app.post("/api/items/{item_id}/star")
def mark_star(item_id: str, db: Db, value: bool | None = Body(default=None, embed=True)):
    return _set_item_flag(db, item_id, "starred", value)


@app.post("/api/items/{item_id}/hide")
def mark_hide(item_id: str, db: Db, value: bool | None = Body(default=None, embed=True)):
    return _set_item_flag(db, item_id, "hidden", value)


@app.post("/api/items/{item_id}/resummarize")
def resummarize(item_id: str, db: Db):
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    settings = load_runtime_settings(db)
    item.summary_status = SummaryStatus.pending.value if settings.llm_configured else SummaryStatus.not_configured.value
    db.commit()
    if settings.llm_configured:
        queue_job(db, "summarize_item", {"item_id": item_id})
    return item_to_out(item, db)


@app.get("/api/sources", response_model=list[SourceOut])
def get_sources(db: Db):
    return list_sources(db)


@app.post("/api/sources", response_model=SourceOut)
def create_source(payload: SourceIn, db: Db):
    if db.get(Source, payload.id):
        raise HTTPException(status_code=409, detail="Source id already exists")
    source = create_source_model(payload)
    db.add(source)
    db.commit()
    db.refresh(source)
    queue_auto_summaries(db, load_runtime_settings(db), source_id=source.id, limit=20)
    return source_to_out(source)


@app.patch("/api/sources/{source_id}", response_model=SourceOut)
def update_source(source_id: str, payload: SourcePatch, db: Db):
    source = db.execute(select(Source).options(selectinload(Source.attempts)).where(Source.id == source_id)).scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    patch_source(db, source, payload)
    db.commit()
    db.refresh(source)
    if payload.auto_summary_enabled is not None or payload.auto_summary_days is not None:
        queue_auto_summaries(db, load_runtime_settings(db), source_id=source.id, limit=20)
    return source_to_out(source)


@app.post("/api/sources/{source_id}/fetch")
def fetch_source_now(source_id: str, db: Db):
    if not db.get(Source, source_id):
        raise HTTPException(status_code=404, detail="Source not found")
    job = queue_job(db, "fetch_source", {"source_id": source_id})
    return {"job_id": job.id, "status": job.status}


@app.post("/api/sources/preview", response_model=PreviewResponse)
async def source_preview(payload: PreviewRequest, db: Db):
    try:
        result = await preview_source(payload.url, payload.route, payload.adapter, load_runtime_settings(db))
    except AdapterError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": exc.message}) from exc
    entries = [
        {
            "title": entry.title,
            "url": entry.url,
            "published_at": entry.published_at,
            "authors": entry.authors,
            "summary": entry.summary,
            "has_text": bool(entry.content or entry.summary),
        }
        for entry in result.entries[:5]
    ]
    return PreviewResponse(detected_adapter=payload.adapter, entries=entries, warnings=result.warnings, used_url=result.used_url)


@app.post("/api/sources/import")
def import_sources(db: Db, source_pack: str = Body(media_type="text/yaml")):
    imported = import_source_pack(db, source_pack)
    queued = queue_auto_summaries(db, load_runtime_settings(db), limit=20)
    return {"imported": imported, "summary_queued": queued}


@app.get("/api/sources/export")
def export_sources(db: Db):
    return Response(export_source_pack(db), media_type="text/yaml")


JOB_STATUSES = [
    JobStatus.queued.value,
    JobStatus.running.value,
    JobStatus.retrying.value,
    JobStatus.failed.value,
    JobStatus.succeeded.value,
    JobStatus.skipped.value,
]


def _job_target(db: Session, job: Job) -> dict:
    payload = loads(job.payload, {})
    if job.type == "fetch_source":
        source_id = str(payload.get("source_id") or "")
        source = db.get(Source, source_id) if source_id else None
        return {"kind": "source", "id": source_id, "label": source.name if source else source_id or "Unknown source"}
    if job.type == "summarize_item":
        item_id = str(payload.get("item_id") or "")
        item = db.get(Item, item_id) if item_id else None
        if item:
            label = item.title or item_id
            if item.source_name:
                label = f"{label} ({item.source_name})"
            return {"kind": "item", "id": item_id, "label": label}
        return {"kind": "item", "id": item_id, "label": item_id or "Unknown item"}
    return {"kind": "payload", "id": "", "label": dumps(payload)[:240]}


def _job_to_health(db: Session, job: Job) -> dict:
    return {
        "id": job.id,
        "type": job.type,
        "status": job.status,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "scheduled_at": job.scheduled_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "target": _job_target(db, job),
    }


def _job_health(db: Session) -> dict:
    count_rows = db.execute(select(Job.status, func.count(Job.id)).group_by(Job.status)).all()
    counts = {status: 0 for status in JOB_STATUSES}
    for status, count in count_rows:
        counts[str(status)] = int(count or 0)

    active_jobs = (
        db.execute(
            select(Job)
            .where(Job.status.in_([JobStatus.running.value, JobStatus.retrying.value, JobStatus.queued.value]))
            .order_by(Job.scheduled_at, Job.id)
            .limit(20)
        )
        .scalars()
        .all()
    )
    recent_jobs = (
        db.execute(
            select(Job)
            .where(Job.status.in_([JobStatus.succeeded.value, JobStatus.failed.value, JobStatus.skipped.value]))
            .order_by(Job.finished_at.desc().nullslast(), Job.id.desc())
            .limit(10)
        )
        .scalars()
        .all()
    )
    return {
        "counts": counts,
        "active": [_job_to_health(db, job) for job in active_jobs],
        "recent": [_job_to_health(db, job) for job in recent_jobs],
    }


@app.get("/api/health")
def health(db: Db):
    runs = db.execute(select(SourceRun).order_by(SourceRun.id.desc()).limit(50)).scalars().all()
    sources = db.execute(select(Source)).scalars().all()
    items_total = db.execute(select(func.count(Item.id))).scalar_one()
    items_24h = db.execute(select(func.count(Item.id)).where(Item.created_at >= datetime.now(timezone.utc) - timedelta(days=1))).scalar_one()
    summary_total = db.execute(select(func.count(Summary.id))).scalar_one()
    summary_failed = db.execute(select(func.count(Summary.id)).where(Summary.status == "failed")).scalar_one()
    latest_by_source = latest_runs(db)
    latest_success_rows = db.execute(
        select(SourceRun.source_id, func.max(SourceRun.finished_at))
        .where(SourceRun.status == "succeeded")
        .group_by(SourceRun.source_id)
    ).all()
    latest_success_by_source = {source_id: finished_at for source_id, finished_at in latest_success_rows}
    content_stats = source_content_stats(db)
    summary_stats = source_summary_stats(db)
    source_health = []
    degraded_sources = []
    for source in sources:
        latest = latest_by_source.get(source.id)
        recent_source_runs = [run for run in runs if run.source_id == source.id]
        consecutive_failures = 0
        consecutive_empty = 0
        for run in recent_source_runs:
            if run.status == "failed":
                consecutive_failures += 1
            else:
                break
        for run in recent_source_runs:
            if run.status == "empty":
                consecutive_empty += 1
            else:
                break
        stats = content_stats.get(source.id, {})
        summaries = summary_stats.get(source.id, {})
        item_count = int(stats.get("item_count") or 0)
        fulltext_success_count = int(stats.get("feed_count") or 0) + int(stats.get("detail_count") or 0)
        if item_count:
            fulltext_success_count = min(fulltext_success_count, item_count)
        fulltext_success_rate = (fulltext_success_count / item_count) if item_count else None
        summary_ready_count = int(summaries.get(SummaryStatus.ready.value) or 0)
        summary_failed_count = int(summaries.get(SummaryStatus.failed.value) or 0)
        source_summary_total = sum(summaries.values())
        summary_failure_rate = (summary_failed_count / source_summary_total) if source_summary_total else None
        content_audit = content_audit_for_source(source, latest, stats)
        degraded_reasons = []
        if latest and latest.status == "failed":
            degraded_reasons.append("fetch_failed")
        if consecutive_empty:
            degraded_reasons.append("empty_results")
        if source.content_type != "paper" and content_audit.get("status") in {"fetch_failed", "feed_title_only", "feed_summary_only"}:
            degraded_reasons.append("fulltext_incomplete")
        if summary_failure_rate is not None and summary_failed_count and summary_failure_rate > 0.2:
            degraded_reasons.append("summary_failures")
        if degraded_reasons:
            degraded_sources.append({"id": source.id, "name": source.name, "reason": ", ".join(degraded_reasons)})
        source_health.append(
            {
                "id": source.id,
                "name": source.name,
                "enabled": source.enabled,
                "auto_summary_enabled": source.auto_summary_enabled,
                "auto_summary_days": source.auto_summary_days,
                "content_audit": content_audit,
                "latest_success_at": latest_success_by_source.get(source.id),
                "raw_count": latest.raw_count if latest else 0,
                "item_count": item_count,
                "fulltext_success_count": fulltext_success_count,
                "fulltext_success_rate": fulltext_success_rate,
                "summary_ready_count": summary_ready_count,
                "summary_failed_count": summary_failed_count,
                "summary_failure_rate": summary_failure_rate,
                "latest_run": {
                    "id": latest.id,
                    "status": latest.status,
                    "finished_at": latest.finished_at,
                    "raw_count": latest.raw_count,
                    "item_count": latest.item_count,
                    "fulltext_success_count": latest.fulltext_success_count,
                    "error_message": latest.error_message,
                }
                if latest
                else None,
                "consecutive_failures": consecutive_failures,
                "consecutive_empty": consecutive_empty,
            }
        )
    settings = load_runtime_settings(db)
    llm_usage = llm_usage_stats(db)
    ai_available = False
    ai_last_error = ""
    if settings.llm_provider_type == "codex_cli":
        ai_available = bool(shutil.which(settings.codex_cli_path))
        if not ai_available:
            ai_last_error = f"Codex CLI not found: {settings.codex_cli_path}"
    elif settings.llm_provider_type == "openai_compatible":
        ai_available = settings.llm_configured
        if not ai_available:
            ai_last_error = "OpenAI-compatible provider is not fully configured."
    recent_summary_errors = db.execute(
        select(Summary, Item)
        .join(Item, Item.id == Summary.item_id)
        .where(Summary.error_message != "")
        .order_by(Summary.id.desc())
        .limit(10)
    ).all()
    return {
        "ok": True,
        "items_total": items_total,
        "items_24h": items_24h,
        "jobs": _job_health(db),
        "summary": {
            "total": summary_total,
            "failed": summary_failed,
            "failure_rate": (summary_failed / summary_total) if summary_total else 0,
        },
        "ai_provider": {
            "type": settings.llm_provider_type,
            "configured": settings.llm_configured,
            "available": ai_available,
            "model": settings.llm_model_name or settings.codex_cli_model,
            "last_error": ai_last_error,
            "usage": llm_usage,
        },
        "sources": source_health,
        "degraded_sources": degraded_sources,
        "recent_errors": [
            {"source_id": run.source_id, "error_code": run.error_code, "error_message": run.error_message, "finished_at": run.finished_at}
            for run in runs
            if run.error_message
        ][:10],
        "recent_summary_errors": [
            {
                "item_id": summary.item_id,
                "source_id": item.source_id,
                "title": item.title,
                "provider": summary.provider,
                "model": summary.model,
                "error_message": summary.error_message,
                "created_at": summary.created_at,
            }
            for summary, item in recent_summary_errors
        ],
    }


@app.get("/api/settings", response_model=SettingsOut)
def get_app_settings(db: Db):
    settings = load_runtime_settings(db)
    return SettingsOut(
        database_url=_redact_database_url(settings.database_url),
        rsshub_public_instances=[x for x in settings.rsshub_instances if x != (settings.rsshub_self_hosted_base_url or "").rstrip("/")],
        rsshub_self_hosted_base_url=settings.rsshub_self_hosted_base_url,
        llm_provider_type=settings.llm_provider_type,
        llm_configured=settings.llm_configured,
        llm_base_url=settings.llm_base_url,
        llm_model_name=settings.llm_model_name,
        codex_cli_path=settings.codex_cli_path,
        codex_cli_model=settings.codex_cli_model,
        llm_usage=llm_usage_stats(db),
    )


@app.patch("/api/settings")
def patch_app_settings(payload: SettingsPatch, db: Db):
    for key, value in payload.model_dump(exclude_unset=True).items():
        if key == "llm_api_key" and (value is None or not value.strip()):
            continue
        stored = db.get(Setting, key)
        if not stored:
            stored = Setting(key=key)
            db.add(stored)
        stored.value = dumps(value)
    db.commit()
    return {"saved": True, "note": "AI runtime settings are stored in DB. RSSHub is configured by environment or config files."}


def _settings_for_ai_test(db: Session, payload: SettingsPatch):
    base_settings = load_runtime_settings(db)
    overrides = payload.model_dump(exclude_unset=True)
    if overrides.get("llm_api_key") == "":
        overrides.pop("llm_api_key")
    return base_settings.model_copy(update=overrides)


def _redact_database_url(database_url: str) -> str:
    parsed = urlsplit(database_url)
    if not parsed.netloc or "@" not in parsed.netloc:
        return database_url
    credentials, host = parsed.netloc.rsplit("@", 1)
    username = credentials.split(":", 1)[0]
    redacted_credentials = f"{username}:***" if username else "***"
    return urlunsplit((parsed.scheme, f"{redacted_credentials}@{host}", parsed.path, parsed.query, parsed.fragment))


def _ai_test_item() -> Item:
    return Item(
        source_id="settings-test",
        canonical_url="settings-test://ai-provider",
        title="AI provider smoke test",
        url="settings-test://ai-provider",
        content_type="blog",
        platform="settings",
        source_name="Settings",
        summary="This item verifies whether the configured summary provider can return valid JSON.",
        raw_text="Daily Info collects AI research updates. Reply with a short Chinese summary in the requested JSON schema.",
    )


@app.post("/api/settings/test-ai", response_model=AiProviderTestResult)
async def test_ai_provider(payload: SettingsPatch, db: Db):
    settings = _settings_for_ai_test(db, payload)
    provider = settings.llm_provider_type
    model = settings.llm_model_name if provider == "openai_compatible" else settings.codex_cli_model
    if provider == "none":
        return AiProviderTestResult(ok=False, provider=provider, model=model, error="Summary AI is disabled.")
    try:
        if provider == "openai_compatible":
            result = await summarize_openai_compatible(_ai_test_item(), settings)
        elif provider == "codex_cli":
            result = await summarize_codex_cli(_ai_test_item(), settings)
        else:
            return AiProviderTestResult(ok=False, provider=provider, model=model, error=f"Unsupported provider: {provider}")
    except Exception as exc:
        return AiProviderTestResult(ok=False, provider=provider, model=model, error=str(exc))
    return AiProviderTestResult(
        ok=True,
        provider=provider,
        model=model,
        duration_ms=int(result.get("duration_ms") or 0),
        usage=result.get("usage") if isinstance(result.get("usage"), dict) else {},
    )


@app.post("/api/scheduler/run")
def run_scheduler_once(db: Db):
    settings = load_runtime_settings(db)
    return {"scheduled": schedule_due_sources(db), "summary_queued": schedule_auto_summaries(db, settings)}


@app.get("/api/clusters")
def get_clusters():
    return {"clusters": []}
