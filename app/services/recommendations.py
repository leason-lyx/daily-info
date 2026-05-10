"""Profile, candidate, scoring, trend, embedding, and cache helpers."""

from app.services.legacy import (  # noqa: F401
    RECOMMENDATION_EMBEDDING_MODEL,
    RECOMMENDATION_MODEL_VERSION,
    RECOMMENDATION_PROFILE_ID,
    RECOMMENDATION_SCORE_TTL_MINUTES,
    RECOMMENDATION_WEIGHTS,
    build_recommendation_profile,
    embed_item,
    get_recommendation_profile,
    item_event_to_dict,
    patch_recommendation_profile,
    recommendation_embedding_text,
    recommendation_for_item,
    recommendation_profile_weights,
    record_item_event,
    refresh_external_trends,
    score_recommendations,
    upsert_external_trend_signal,
)

