"""Ingestion, deterministic dedupe, provenance merge, and work intents."""

from app.services.core import (  # noqa: F401
    IngestResult,
    TaggingResult,
    WorkIntent,
    canonical_url_for_entry,
    dedupe_key_for_entry,
    persist_entries,
    upsert_item_source,
)

