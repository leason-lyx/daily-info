from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event, text
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
    _upgrade_sqlite_schema()


def _upgrade_sqlite_schema() -> None:
    if engine.dialect.name != "sqlite":
        return
    with engine.begin() as connection:
        _add_missing_sqlite_columns(
            connection,
            "sources",
            {
                "tagging": "TEXT DEFAULT '{\"mode\":\"llm\",\"max_tags\":5}'",
                "fetch": "TEXT DEFAULT '{}'",
                "summary": "TEXT DEFAULT '{}'",
                "auth": "TEXT DEFAULT '{\"mode\":\"none\"}'",
                "spec_json": "TEXT DEFAULT '{}'",
                "spec_hash": "VARCHAR(64) DEFAULT ''",
                "catalog_file": "TEXT DEFAULT ''",
            },
        )
        _add_missing_sqlite_columns(
            connection,
            "llm_providers",
            {
                "name": "VARCHAR(120) DEFAULT 'Custom API'",
                "api_key": "TEXT DEFAULT ''",
                "priority": "INTEGER DEFAULT 0",
                "created_at": "DATETIME",
            },
        )
        _add_missing_sqlite_columns(
            connection,
            "items",
            {
                "dedupe_key": "VARCHAR(180) DEFAULT ''",
            },
        )
        if _sqlite_columns(connection, "items") and "dedupe_key" in _sqlite_columns(connection, "items"):
            connection.exec_driver_sql("UPDATE items SET dedupe_key = 'legacy:' || id WHERE dedupe_key IS NULL OR dedupe_key = ''")
        _seed_subscriptions_from_legacy_enabled(connection)


def _add_missing_sqlite_columns(connection, table: str, columns: dict[str, str]) -> None:
    existing = _sqlite_columns(connection, table)
    if not existing:
        return
    for name, definition in columns.items():
        if name not in existing:
            connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _sqlite_columns(connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(text(f"PRAGMA table_info({table})"))}


def _seed_subscriptions_from_legacy_enabled(connection) -> None:
    source_columns = _sqlite_columns(connection, "sources")
    subscription_columns = _sqlite_columns(connection, "source_subscriptions")
    if not source_columns or not subscription_columns or "enabled" not in source_columns:
        return
    connection.exec_driver_sql(
        """
        INSERT OR IGNORE INTO source_subscriptions (source_id, subscribed, settings_override, created_at, updated_at)
        SELECT id, 1, '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        FROM sources
        WHERE enabled = 1
        """
    )


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
