"""Typed job names and enqueue helpers."""

from app.job_queue import (  # noqa: F401
    JobSpec,
    enqueue_embed_item,
    enqueue_fetch_source,
    enqueue_refresh_trends,
    enqueue_score_recommendations,
    enqueue_summarize_item,
)

FETCH_SOURCE = "fetch_source"
SUMMARIZE_ITEM = "summarize_item"
EMBED_ITEM = "embed_item"
REFRESH_EXTERNAL_TRENDS = "refresh_external_trends"
BUILD_RECOMMENDATION_PROFILE = "build_recommendation_profile"
SCORE_RECOMMENDATIONS = "score_recommendations"

