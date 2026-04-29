from collections.abc import Generator
import json
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _engine_kwargs(database_url: str) -> dict:
    if database_url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False, "timeout": 30}}
    return {}


def _ensure_sqlite_parent_dir(database_url: str) -> None:
    if not database_url.startswith("sqlite:///"):
        return
    path = database_url.removeprefix("sqlite:///")
    if not path or path == ":memory:":
        return
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)


settings = get_settings()
_ensure_sqlite_parent_dir(settings.database_url)
engine = create_engine(settings.database_url, future=True, **_engine_kwargs(settings.database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _connection_record):
    if settings.database_url.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _migrate_sqlite_schema()


def _migrate_sqlite_schema() -> None:
    if not settings.database_url.startswith("sqlite"):
        return
    with engine.begin() as conn:
        inspector = inspect(conn)
        tables = set(inspector.get_table_names())
        if "sources" in tables:
            source_columns = {column["name"] for column in inspector.get_columns("sources")}
            if "auto_summary_enabled" not in source_columns:
                _add_sqlite_column(conn, "ALTER TABLE sources ADD COLUMN auto_summary_enabled BOOLEAN NOT NULL DEFAULT 0")
                conn.execute(
                    text(
                        "UPDATE sources "
                        "SET auto_summary_enabled = CASE WHEN content_type IN ('blog', 'post', 'news') THEN 1 ELSE 0 END"
                    )
                )
            if "auto_summary_days" not in source_columns:
                _add_sqlite_column(conn, "ALTER TABLE sources ADD COLUMN auto_summary_days INTEGER NOT NULL DEFAULT 7")
            if "fulltext" not in source_columns:
                _add_sqlite_column(conn, "ALTER TABLE sources ADD COLUMN fulltext TEXT NOT NULL DEFAULT '{\"strategy\":\"feed_field\"}'")
            if "fetch" not in source_columns:
                _add_sqlite_column(conn, "ALTER TABLE sources ADD COLUMN fetch TEXT NOT NULL DEFAULT '{}'")
            if "summary" not in source_columns:
                _add_sqlite_column(conn, "ALTER TABLE sources ADD COLUMN summary TEXT NOT NULL DEFAULT '{}'")
            if "auth" not in source_columns:
                _add_sqlite_column(conn, "ALTER TABLE sources ADD COLUMN auth TEXT NOT NULL DEFAULT '{\"mode\":\"none\"}'")
            if "spec_json" not in source_columns:
                _add_sqlite_column(conn, "ALTER TABLE sources ADD COLUMN spec_json TEXT NOT NULL DEFAULT '{}'")
            if "spec_hash" not in source_columns:
                _add_sqlite_column(conn, "ALTER TABLE sources ADD COLUMN spec_hash VARCHAR(64) NOT NULL DEFAULT ''")
            if "catalog_file" not in source_columns:
                _add_sqlite_column(conn, "ALTER TABLE sources ADD COLUMN catalog_file TEXT NOT NULL DEFAULT ''")
            if "auth_mode" not in source_columns:
                _add_sqlite_column(conn, "ALTER TABLE sources ADD COLUMN auth_mode VARCHAR(40) NOT NULL DEFAULT 'none'")
            if "stability_level" not in source_columns:
                _add_sqlite_column(conn, "ALTER TABLE sources ADD COLUMN stability_level VARCHAR(40) NOT NULL DEFAULT 'stable'")
        if {"sources", "source_subscriptions"}.issubset(tables):
            conn.execute(
                text(
                    "INSERT INTO source_subscriptions (source_id, subscribed, priority_override, settings_override, created_at, updated_at) "
                    "SELECT sources.id, 1, NULL, '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
                    "FROM sources "
                    "LEFT JOIN source_subscriptions ON source_subscriptions.source_id = sources.id "
                    "WHERE sources.enabled = 1 AND source_subscriptions.source_id IS NULL"
                )
            )
        if "source_attempts" in tables:
            attempt_columns = {column["name"] for column in inspector.get_columns("source_attempts")}
            if "config" not in attempt_columns:
                _add_sqlite_column(conn, "ALTER TABLE source_attempts ADD COLUMN config TEXT NOT NULL DEFAULT '{}'")
        if "source_runs" in tables:
            run_columns = {column["name"] for column in inspector.get_columns("source_runs")}
            if "fulltext_success_count" not in run_columns:
                _add_sqlite_column(conn, "ALTER TABLE source_runs ADD COLUMN fulltext_success_count INTEGER NOT NULL DEFAULT 0")
            if "summary_queued_count" not in run_columns:
                _add_sqlite_column(conn, "ALTER TABLE source_runs ADD COLUMN summary_queued_count INTEGER NOT NULL DEFAULT 0")
            if "used_rsshub_instance" not in run_columns:
                _add_sqlite_column(conn, "ALTER TABLE source_runs ADD COLUMN used_rsshub_instance TEXT NOT NULL DEFAULT ''")
        if "items" in tables:
            item_columns = {column["name"] for column in inspector.get_columns("items")}
            if "dedupe_key" not in item_columns:
                _add_sqlite_column(conn, "ALTER TABLE items ADD COLUMN dedupe_key VARCHAR(180)")
        if "summaries" in tables:
            summary_columns = {column["name"] for column in inspector.get_columns("summaries")}
            for column in ["prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens", "duration_ms"]:
                if column not in summary_columns:
                    _add_sqlite_column(conn, f"ALTER TABLE summaries ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")
            if "usage_json" not in summary_columns:
                _add_sqlite_column(conn, "ALTER TABLE summaries ADD COLUMN usage_json TEXT NOT NULL DEFAULT '{}'")
        if "llm_providers" in tables:
            provider_columns = {column["name"] for column in inspector.get_columns("llm_providers")}
            if "name" not in provider_columns:
                _add_sqlite_column(conn, "ALTER TABLE llm_providers ADD COLUMN name VARCHAR(120) NOT NULL DEFAULT 'Custom API'")
            if "api_key" not in provider_columns:
                _add_sqlite_column(conn, "ALTER TABLE llm_providers ADD COLUMN api_key TEXT NOT NULL DEFAULT ''")
            if "priority" not in provider_columns:
                _add_sqlite_column(conn, "ALTER TABLE llm_providers ADD COLUMN priority INTEGER NOT NULL DEFAULT 0")
            if "created_at" not in provider_columns:
                _add_sqlite_column(conn, "ALTER TABLE llm_providers ADD COLUMN created_at DATETIME")
        if "items" in tables:
            _backfill_item_dedupe(conn)


def _add_sqlite_column(conn, statement: str) -> None:
    try:
        conn.execute(text(statement))
    except OperationalError as exc:
        if "duplicate column name" not in str(exc).lower():
            raise


def _backfill_item_dedupe(conn) -> None:
    from app.utils import canonicalize_url, dedupe_key_from_parts

    conn.execute(
        text(
            "INSERT OR IGNORE INTO item_sources "
            "(item_id, source_id, source_name, url, canonical_url, tags, first_seen_at, last_seen_at) "
            "SELECT id, source_id, source_name, url, canonical_url, tags, created_at, updated_at FROM items"
        )
    )
    rows = conn.execute(
        text(
            "SELECT id, source_id, canonical_url, url, title, platform, published_at "
            "FROM items WHERE dedupe_key IS NULL OR dedupe_key = ''"
        )
    ).mappings()
    for row in rows:
        key = _dedupe_key_for_existing_item(row, canonicalize_url, dedupe_key_from_parts)
        conn.execute(text("UPDATE items SET dedupe_key = :key WHERE id = :id"), {"key": key, "id": row["id"]})
    duplicate_rows = conn.execute(
        text(
            "SELECT dedupe_key FROM items "
            "WHERE dedupe_key IS NOT NULL AND dedupe_key != '' "
            "GROUP BY dedupe_key HAVING COUNT(*) > 1"
        )
    ).mappings()
    for duplicate in duplicate_rows:
        item_rows = list(
            conn.execute(
                text(
                    "SELECT id, created_at, title, chinese_title, authors, summary, raw_text, tags, entities, "
                    "read, starred, hidden, summary_status, published_at "
                    "FROM items WHERE dedupe_key = :key ORDER BY created_at, id"
                ),
                {"key": duplicate["dedupe_key"]},
            ).mappings()
        )
        if len(item_rows) < 2:
            continue
        keep = item_rows[0]
        for item in item_rows[1:]:
            _merge_duplicate_item(conn, keep["id"], item)
    conn.execute(text("DROP INDEX IF EXISTS uq_items_dedupe_key"))
    conn.execute(text("CREATE UNIQUE INDEX uq_items_dedupe_key ON items(dedupe_key)"))


def _dedupe_key_for_existing_item(row, canonicalize_url, dedupe_key_from_parts) -> str:
    canonical = canonicalize_url(row["canonical_url"] or row["url"] or "")
    return dedupe_key_from_parts(
        canonical,
        row["title"] or "",
        row["published_at"],
        row["platform"] or row["source_id"] or "source",
        row["canonical_url"] or "",
        row["url"] or "",
    )


def _merge_duplicate_item(conn, keep_id: str, item) -> None:
    duplicate_id = item["id"]
    source_rows = conn.execute(
        text("SELECT source_id, source_name, url, canonical_url, tags, first_seen_at, last_seen_at FROM item_sources WHERE item_id = :item_id"),
        {"item_id": duplicate_id},
    ).mappings()
    for source_row in source_rows:
        _upsert_migrated_item_source(conn, keep_id, source_row)
    conn.execute(text("UPDATE summaries SET item_id = :keep_id WHERE item_id = :duplicate_id"), {"keep_id": keep_id, "duplicate_id": duplicate_id})
    conn.execute(text("UPDATE fulltexts SET item_id = :keep_id WHERE item_id = :duplicate_id"), {"keep_id": keep_id, "duplicate_id": duplicate_id})
    conn.execute(text("UPDATE cluster_items SET item_id = :keep_id WHERE item_id = :duplicate_id"), {"keep_id": keep_id, "duplicate_id": duplicate_id})
    _move_summary_jobs(conn, duplicate_id, keep_id)
    keep = conn.execute(text("SELECT * FROM items WHERE id = :keep_id"), {"keep_id": keep_id}).mappings().one()
    conn.execute(
        text(
            "UPDATE items SET "
            "read = CASE WHEN read OR :read THEN 1 ELSE 0 END, "
            "starred = CASE WHEN starred OR :starred THEN 1 ELSE 0 END, "
            "hidden = CASE WHEN hidden OR :hidden THEN 1 ELSE 0 END, "
            "chinese_title = CASE WHEN length(coalesce(chinese_title, '')) >= :chinese_title_len THEN chinese_title ELSE :chinese_title END, "
            "authors = :authors, "
            "summary = CASE WHEN length(coalesce(summary, '')) >= :summary_len THEN summary ELSE :summary END, "
            "raw_text = CASE WHEN length(coalesce(raw_text, '')) >= :raw_len THEN raw_text ELSE :raw_text END, "
            "tags = :tags, "
            "entities = :entities, "
            "summary_status = :summary_status, "
            "published_at = coalesce(published_at, :published_at) "
            "WHERE id = :keep_id"
        ),
        {
            "keep_id": keep_id,
            "read": int(bool(item["read"])),
            "starred": int(bool(item["starred"])),
            "hidden": int(bool(item["hidden"])),
            "chinese_title": item["chinese_title"] or "",
            "chinese_title_len": len(item["chinese_title"] or ""),
            "authors": _merge_json_lists(keep["authors"], item["authors"]),
            "summary": item["summary"] or "",
            "summary_len": len(item["summary"] or ""),
            "raw_text": item["raw_text"] or "",
            "raw_len": len(item["raw_text"] or ""),
            "tags": _merge_json_lists(keep["tags"], item["tags"]),
            "entities": _merge_json_lists(keep["entities"], item["entities"]),
            "summary_status": _best_summary_status(keep["summary_status"], item["summary_status"]),
            "published_at": item["published_at"],
        },
    )
    conn.execute(text("DELETE FROM item_sources WHERE item_id = :duplicate_id"), {"duplicate_id": duplicate_id})
    conn.execute(text("DELETE FROM items WHERE id = :duplicate_id"), {"duplicate_id": duplicate_id})


def _upsert_migrated_item_source(conn, keep_id: str, source_row) -> None:
    existing = conn.execute(
        text("SELECT * FROM item_sources WHERE item_id = :item_id AND source_id = :source_id"),
        {"item_id": keep_id, "source_id": source_row["source_id"]},
    ).mappings().first()
    if not existing:
        conn.execute(
            text(
                "INSERT INTO item_sources "
                "(item_id, source_id, source_name, url, canonical_url, tags, first_seen_at, last_seen_at) "
                "VALUES (:item_id, :source_id, :source_name, :url, :canonical_url, :tags, :first_seen_at, :last_seen_at)"
            ),
            {
                "item_id": keep_id,
                "source_id": source_row["source_id"],
                "source_name": source_row["source_name"],
                "url": source_row["url"],
                "canonical_url": source_row["canonical_url"],
                "tags": source_row["tags"],
                "first_seen_at": source_row["first_seen_at"],
                "last_seen_at": source_row["last_seen_at"],
            },
        )
        return
    conn.execute(
        text(
            "UPDATE item_sources SET "
            "source_name = coalesce(nullif(source_name, ''), :source_name), "
            "url = coalesce(nullif(url, ''), :url), "
            "canonical_url = coalesce(nullif(canonical_url, ''), :canonical_url), "
            "tags = :tags, "
            "first_seen_at = min(first_seen_at, :first_seen_at), "
            "last_seen_at = max(last_seen_at, :last_seen_at) "
            "WHERE id = :id"
        ),
        {
            "id": existing["id"],
            "source_name": source_row["source_name"],
            "url": source_row["url"],
            "canonical_url": source_row["canonical_url"],
            "tags": _merge_json_lists(existing["tags"], source_row["tags"]),
            "first_seen_at": source_row["first_seen_at"],
            "last_seen_at": source_row["last_seen_at"],
        },
    )


def _move_summary_jobs(conn, duplicate_id: str, keep_id: str) -> None:
    rows = conn.execute(text("SELECT id, payload FROM jobs WHERE type = 'summarize_item'")).mappings()
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except json.JSONDecodeError:
            continue
        if payload.get("item_id") != duplicate_id:
            continue
        payload["item_id"] = keep_id
        conn.execute(text("UPDATE jobs SET payload = :payload WHERE id = :id"), {"id": row["id"], "payload": json.dumps(payload, separators=(",", ":"))})


def _merge_json_lists(left: str | None, right: str | None) -> str:
    values: list[str] = []
    seen: set[str] = set()
    for raw in [left, right]:
        try:
            items = json.loads(raw or "[]")
        except json.JSONDecodeError:
            items = []
        for item in items if isinstance(items, list) else []:
            value = str(item).strip()
            if value and value not in seen:
                seen.add(value)
                values.append(value)
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def _best_summary_status(left: str, right: str) -> str:
    rank = {"ready": 5, "pending": 4, "failed": 3, "skipped": 2, "not_configured": 1}
    return left if rank.get(left, 0) >= rank.get(right, 0) else right


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
