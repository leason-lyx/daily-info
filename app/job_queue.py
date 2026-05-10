from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.context import DEFAULT_PROFILE_ID
from app.models import Job, JobStatus
from app.utils import dumps


def queue_job(
    db: Session,
    job_type: str,
    payload: dict[str, Any],
    max_attempts: int = 3,
    *,
    idempotency_key: str,
    queue: str = "default",
    priority: int = 100,
) -> Job:
    rendered_payload = dumps(payload)
    existing = db.execute(
        select(Job).where(
            Job.type == job_type,
            Job.idempotency_key == idempotency_key,
            Job.status.in_([JobStatus.queued.value, JobStatus.running.value, JobStatus.retrying.value]),
        )
    ).scalar_one_or_none()
    if existing:
        setattr(existing, "_queue_created", False)
        return existing
    job = Job(
        type=job_type,
        payload=rendered_payload,
        max_attempts=max_attempts,
        idempotency_key=idempotency_key,
        queue=queue,
        priority=priority,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    setattr(job, "_queue_created", True)
    return job


def enqueue_fetch_source(db: Session, source_id: str) -> Job:
    return queue_job(db, "fetch_source", {"source_id": source_id}, idempotency_key=f"fetch_source:{source_id}")


def enqueue_summarize_item(db: Session, item_id: str) -> Job:
    return queue_job(db, "summarize_item", {"item_id": item_id}, idempotency_key=f"summarize_item:{item_id}")


def enqueue_embed_item(db: Session, item_id: str) -> Job:
    return queue_job(db, "embed_item", {"item_id": item_id}, max_attempts=2, idempotency_key=f"embed_item:{item_id}")


def enqueue_refresh_trends(db: Session, profile_id: str = DEFAULT_PROFILE_ID) -> Job:
    return queue_job(
        db,
        "refresh_external_trends",
        {"profile_id": profile_id},
        max_attempts=2,
        idempotency_key=f"refresh_external_trends:{profile_id}",
    )


def enqueue_build_recommendation_profile(db: Session, profile_id: str = DEFAULT_PROFILE_ID) -> Job:
    return queue_job(
        db,
        "build_recommendation_profile",
        {"profile_id": profile_id},
        max_attempts=1,
        idempotency_key=f"build_recommendation_profile:{profile_id}",
    )


def enqueue_score_recommendations(db: Session, profile_id: str = DEFAULT_PROFILE_ID) -> Job:
    return queue_job(
        db,
        "score_recommendations",
        {"profile_id": profile_id},
        idempotency_key=f"score_recommendations:{profile_id}",
    )
