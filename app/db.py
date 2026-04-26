from collections.abc import Generator
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
            if "auth_mode" not in source_columns:
                _add_sqlite_column(conn, "ALTER TABLE sources ADD COLUMN auth_mode VARCHAR(40) NOT NULL DEFAULT 'none'")
            if "stability_level" not in source_columns:
                _add_sqlite_column(conn, "ALTER TABLE sources ADD COLUMN stability_level VARCHAR(40) NOT NULL DEFAULT 'stable'")
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
        if "summaries" in tables:
            summary_columns = {column["name"] for column in inspector.get_columns("summaries")}
            for column in ["prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens", "duration_ms"]:
                if column not in summary_columns:
                    _add_sqlite_column(conn, f"ALTER TABLE summaries ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")
            if "usage_json" not in summary_columns:
                _add_sqlite_column(conn, "ALTER TABLE summaries ADD COLUMN usage_json TEXT NOT NULL DEFAULT '{}'")


def _add_sqlite_column(conn, statement: str) -> None:
    try:
        conn.execute(text(statement))
    except OperationalError as exc:
        if "duplicate column name" not in str(exc).lower():
            raise


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
