import argparse
import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.catalog import DEFAULT_SOURCE_PACK_PATH
from app.config import get_settings
from app.db import SessionLocal, init_db
from app.jobs import fetch_source_job
from app.models import Fulltext, Item, Source
from app.services import content_audit_for_source, latest_runs, load_source_pack, sync_default_source_pack, source_content_stats
from app.utils import dumps, loads


def sqlite_path(database_url: str) -> Path:
    parsed = urlparse(database_url)
    if parsed.scheme != "sqlite":
        raise RuntimeError("source audit backup only supports sqlite DATABASE_URL")
    if database_url.startswith("sqlite:////"):
        return Path(parsed.path)
    if database_url.startswith("sqlite:///"):
        return Path(database_url.removeprefix("sqlite:///"))
    raise RuntimeError(f"Unsupported sqlite URL: {database_url}")


def backup_sqlite_database(database_url: str) -> Path:
    db_path = sqlite_path(database_url)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = db_path.with_name(f"{db_path.stem}.{timestamp}.backup{db_path.suffix}")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as source, sqlite3.connect(backup_path) as target:
        source.backup(target)
    return backup_path


def apply_recommended_default_strategies() -> list[str]:
    defaults = {source.id: source for source in load_source_pack(DEFAULT_SOURCE_PACK_PATH)}
    changed: list[str] = []
    with SessionLocal() as db:
        sources = db.execute(select(Source).options(selectinload(Source.attempts))).scalars().all()
        for source in sources:
            default_source = defaults.get(source.id)
            if not default_source:
                continue
            next_fulltext = default_source.fulltext
            if loads(source.fulltext, {}) != next_fulltext:
                source.fulltext = dumps(next_fulltext)
                changed.append(f"{source.id}: fulltext")
            configs = {(attempt.adapter, attempt.route or attempt.url): attempt.config for attempt in default_source.attempts}
            for attempt in source.attempts:
                key = (attempt.adapter, attempt.route or attempt.url)
                if key not in configs:
                    continue
                if loads(attempt.config, {}) != configs[key]:
                    attempt.config = dumps(configs[key])
                    changed.append(f"{source.id}: attempt {attempt.id} config")
        if changed:
            db.commit()
    return changed


async def fetch_all_sources() -> list[dict]:
    settings = get_settings().model_copy(update={"llm_provider_type": "none"})
    reports: list[dict] = []
    with SessionLocal() as db:
        sources = db.execute(select(Source).order_by(Source.group, Source.priority, Source.id)).scalars().all()
        source_ids = [source.id for source in sources]
    for source_id in source_ids:
        with SessionLocal() as db:
            run = await fetch_source_job(db, source_id, settings)
            reports.append(
                {
                    "source_id": source_id,
                    "status": run.status,
                    "raw_count": run.raw_count,
                    "item_count": run.item_count,
                    "fulltext_success_count": run.fulltext_success_count,
                    "error_code": run.error_code,
                    "error_message": run.error_message,
                    "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                }
            )
    return reports


def build_audit_report(fetch_reports: list[dict], backup_path: Path | None, strategy_changes: list[str]) -> dict:
    with SessionLocal() as db:
        runs = latest_runs(db)
        stats_by_source = source_content_stats(db)
        sources = db.execute(select(Source).order_by(Source.group, Source.priority, Source.id)).scalars().all()
        source_reports = []
        for source in sources:
            stats = stats_by_source.get(source.id, {})
            latest = runs.get(source.id)
            extractor_rows = db.execute(
                select(Fulltext.extractor, Fulltext.status, Fulltext.error_message)
                .join(Item, Item.id == Fulltext.item_id)
                .where(Item.source_id == source.id)
                .order_by(Fulltext.id.desc())
                .limit(6)
            ).all()
            samples = db.execute(
                select(Item.title, Item.url, Item.summary, Item.raw_text)
                .where(Item.source_id == source.id)
                .order_by(Item.published_at.desc().nullslast(), Item.created_at.desc())
                .limit(3)
            ).all()
            audit = content_audit_for_source(source, latest, stats)
            source_reports.append(
                {
                    "id": source.id,
                    "name": source.name,
                    "type": source.content_type,
                    "enabled": source.enabled,
                    "content_audit": audit,
                    "latest_run": {
                        "status": latest.status,
                        "raw_count": latest.raw_count,
                        "item_count": latest.item_count,
                        "fulltext_success_count": latest.fulltext_success_count,
                        "error_code": latest.error_code,
                        "error_message": latest.error_message,
                        "finished_at": latest.finished_at.isoformat() if latest.finished_at else None,
                    }
                    if latest
                    else None,
                    "stats": stats,
                    "recent_fulltexts": [
                        {"extractor": extractor, "status": status, "error_message": error}
                        for extractor, status, error in extractor_rows
                    ],
                    "samples": [
                        {
                            "title": title,
                            "url": url,
                            "summary_len": len(summary or ""),
                            "raw_text_len": len(raw_text or ""),
                        }
                        for title, url, summary, raw_text in samples
                    ],
                }
            )
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "backup_path": str(backup_path) if backup_path else None,
        "strategy_changes": strategy_changes,
        "fetch_reports": fetch_reports,
        "sources": source_reports,
    }


async def run(args: argparse.Namespace) -> dict:
    init_db()
    backup_path = backup_sqlite_database(get_settings().database_url) if args.backup else None
    if args.apply_strategies or args.fetch:
        with SessionLocal() as db:
            sync_default_source_pack(db)
    strategy_changes = apply_recommended_default_strategies() if args.apply_strategies else []
    fetch_reports = await fetch_all_sources() if args.fetch else []
    report = build_audit_report(fetch_reports, backup_path, strategy_changes)
    if args.output:
        output_path = Path(args.output)
    else:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_path = Path("artifacts") / f"source-audit-{timestamp}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["report_path"] = str(output_path)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch all sources and audit feed/detail fulltext coverage.")
    parser.add_argument("--no-backup", dest="backup", action="store_false", help="Do not back up the sqlite database before fetching.")
    parser.add_argument("--no-apply-strategies", dest="apply_strategies", action="store_false", help="Do not apply recommended default-pack fulltext strategies.")
    parser.add_argument("--no-fetch", dest="fetch", action="store_false", help="Only report current database state.")
    parser.add_argument("-o", "--output", help="JSON report path. Defaults to artifacts/source-audit-<timestamp>.json.")
    parser.set_defaults(backup=True, apply_strategies=True, fetch=True)
    report = asyncio.run(run(parser.parse_args()))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
