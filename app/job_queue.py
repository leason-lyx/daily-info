from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.context import DEFAULT_PROFILE_ID
from app.models import Job


@dataclass(frozen=True)
class JobSpec:
    job_type: str
    payload: dict[str, Any]
    queue: str = "default"
    priority: int = 100
    max_attempts: int = 3

    @property
    def idempotency_key(self) -> str:
        if self.job_type == "fetch_source":
            return f"fetch_source:{self.payload.get('source_id', '')}"
        if self.job_type in {"summarize_item", "embed_item"}:
            return f"{self.job_type}:{self.payload.get('item_id', '')}"
        if self.job_type in {"refresh_external_trends", "build_recommendation_profile", "score_recommendations"}:
            return f"{self.job_type}:{self.payload.get('profile_id', DEFAULT_PROFILE_ID)}"
        return ""


def enqueue_fetch_source(db: Session, source_id: str) -> Job:
    from app.services.legacy import queue_job

    return queue_job(db, "fetch_source", {"source_id": source_id}, idempotency_key=f"fetch_source:{source_id}")


def enqueue_summarize_item(db: Session, item_id: str) -> Job:
    from app.services.legacy import queue_job

    return queue_job(db, "summarize_item", {"item_id": item_id}, idempotency_key=f"summarize_item:{item_id}")


def enqueue_embed_item(db: Session, item_id: str) -> Job:
    from app.services.legacy import queue_job

    return queue_job(db, "embed_item", {"item_id": item_id}, max_attempts=2, idempotency_key=f"embed_item:{item_id}")


def enqueue_refresh_trends(db: Session, profile_id: str = DEFAULT_PROFILE_ID) -> Job:
    from app.services.legacy import queue_job

    return queue_job(
        db,
        "refresh_external_trends",
        {"profile_id": profile_id},
        max_attempts=2,
        idempotency_key=f"refresh_external_trends:{profile_id}",
    )


def enqueue_score_recommendations(db: Session, profile_id: str = DEFAULT_PROFILE_ID) -> Job:
    from app.services.legacy import queue_job

    return queue_job(
        db,
        "score_recommendations",
        {"profile_id": profile_id},
        idempotency_key=f"score_recommendations:{profile_id}",
    )
