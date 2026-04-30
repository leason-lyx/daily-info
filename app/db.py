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
        source_columns = {row[1] for row in connection.execute(text("PRAGMA table_info(sources)"))}
        if source_columns and "tagging" not in source_columns:
            connection.exec_driver_sql("ALTER TABLE sources ADD COLUMN tagging TEXT DEFAULT '{\"mode\":\"llm\",\"max_tags\":5}'")


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
