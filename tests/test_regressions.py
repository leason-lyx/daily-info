import os
import subprocess
import sys
import textwrap
from pathlib import Path


def run_python(script: str, database_url: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        check=True,
        env=env,
        text=True,
        capture_output=True,
    )


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path}"


def test_docker_context_keeps_source_pack_and_excludes_env_secrets() -> None:
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "Dockerfile.api").read_text(encoding="utf-8")
    dockerignore = (root / ".dockerignore").read_text(encoding="utf-8")

    assert "COPY config ./config" in dockerfile
    assert "config" not in {line.strip().strip("/") for line in dockerignore.splitlines() if line.strip()}
    assert ".env.*" in dockerignore
    assert "!.env.example" in dockerignore
    assert "!.env.local.example" in dockerignore


def test_api_smoke_uses_temp_database(tmp_path: Path) -> None:
    result = run_python(
        """
        from fastapi.testclient import TestClient

        from app.api import app

        with TestClient(app) as client:
            for path in ["/api/items", "/api/sources", "/api/health", "/api/settings", "/api/clusters"]:
                response = client.get(path)
                assert response.status_code == 200, (path, response.status_code, response.text)
        print("ok")
        """,
        sqlite_url(tmp_path / "smoke.db"),
    )
    assert result.stdout.strip() == "ok"


def test_fetch_feed_parses_arxiv_api_atom(tmp_path: Path) -> None:
    result = run_python(
        """
        import asyncio
        from unittest.mock import patch

        import httpx

        from app.adapters import fetch_feed

        atom = b'''<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <id>http://arxiv.org/abs/2604.21771v1</id>
            <title>Generalizing Test Cases for Comprehensive Test Scenario Coverage</title>
            <updated>2026-04-23T15:29:09Z</updated>
            <link href="https://arxiv.org/abs/2604.21771v1" rel="alternate" type="text/html"/>
            <summary>Test cases are essential for software development and maintenance.</summary>
            <published>2026-04-23T15:29:09Z</published>
            <author><name>Binhang Qi</name></author>
            <author><name>Yun Lin</name></author>
          </entry>
        </feed>'''

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def get(self, url, headers=None):
                request = httpx.Request("GET", url)
                return httpx.Response(200, content=atom, request=request)

        with patch("app.adapters.httpx.AsyncClient", FakeAsyncClient):
            result = asyncio.run(fetch_feed("https://export.arxiv.org/api/query?search_query=cat:cs.SE"))

        assert len(result.entries) == 1
        entry = result.entries[0]
        assert entry.title == "Generalizing Test Cases for Comprehensive Test Scenario Coverage"
        assert entry.url == "https://arxiv.org/abs/2604.21771v1"
        assert entry.summary == "Test cases are essential for software development and maintenance."
        assert entry.authors == ["Binhang Qi", "Yun Lin"]
        assert entry.published_at is not None
        print("ok")
        """,
        sqlite_url(tmp_path / "arxiv-api-atom.db"),
    )
    assert result.stdout.strip() == "ok"


def test_sqlite_migration_adds_source_runtime_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "old-schema.db"
    result = run_python(
        f"""
        import sqlite3

        conn = sqlite3.connect({str(db_path)!r})
        conn.executescript(
            '''
            CREATE TABLE sources (
                id VARCHAR(80) NOT NULL,
                name VARCHAR(255) NOT NULL,
                content_type VARCHAR(20) NOT NULL,
                platform VARCHAR(80),
                homepage_url TEXT,
                enabled BOOLEAN,
                is_builtin BOOLEAN,
                "group" VARCHAR(120),
                priority INTEGER,
                poll_interval INTEGER,
                language_hint VARCHAR(20),
                include_keywords TEXT,
                exclude_keywords TEXT,
                default_tags TEXT,
                created_at DATETIME,
                updated_at DATETIME,
                PRIMARY KEY (id)
            );
            INSERT INTO sources (
                id, name, content_type, platform, homepage_url, enabled, is_builtin,
                "group", priority, poll_interval, language_hint, include_keywords,
                exclude_keywords, default_tags, created_at, updated_at
            ) VALUES (
                'legacy-source', 'Legacy Source', 'blog', 'legacy', '', 1, 0,
                'Blogs', 100, 3600, 'auto', '[]', '[]', '[]',
                '2026-01-01T00:00:00', '2026-01-01T00:00:00'
            );
            '''
        )
        conn.close()

        from sqlalchemy import select

        from app.db import SessionLocal, init_db
        from app.models import Source

        init_db()
        with SessionLocal() as db:
            source = db.execute(select(Source).where(Source.id == 'legacy-source')).scalar_one()
            assert source.fulltext == '{{"strategy":"feed_field"}}'
            assert source.auth_mode == 'none'
            assert source.stability_level == 'stable'
        print("ok")
        """,
        sqlite_url(db_path),
    )
    assert result.stdout.strip() == "ok"


def test_fetch_run_counts_only_new_summary_jobs_for_source(tmp_path: Path) -> None:
    result = run_python(
        """
        import asyncio
        from datetime import datetime, timedelta, timezone

        from app.adapters import RawEntryData
        from app.db import SessionLocal, init_db
        from app.jobs import fetch_source_job
        from app.models import Item, Source, SourceAttempt, SummaryStatus
        from app.services import queue_job
        from app.config import get_settings
        from app.utils import dumps

        init_db()
        with SessionLocal() as db:
            other = Source(
                id="other",
                name="Other",
                content_type="blog",
                platform="test",
                enabled=True,
                auto_summary_enabled=True,
                fulltext=dumps({"strategy": "feed_field"}),
            )
            target = Source(
                id="target",
                name="Target",
                content_type="blog",
                platform="test",
                enabled=True,
                auto_summary_enabled=True,
                fulltext=dumps({"strategy": "feed_field"}),
            )
            target.attempts = [SourceAttempt(kind="direct", adapter="manual", enabled=True)]
            db.add_all([other, target])
            db.flush()
            existing = Item(
                source_id="other",
                canonical_url="https://example.com/other",
                title="Other item",
                url="https://example.com/other",
                content_type="blog",
                platform="test",
                source_name="Other",
                raw_text="Other text",
                summary_status=SummaryStatus.pending.value,
            )
            db.add(existing)
            db.flush()
            queue_job(db, "summarize_item", {"item_id": existing.id})
            db.commit()

            async def fake_run_attempt(_attempt, _settings):
                return type(
                    "Result",
                    (),
                    {
                        "entries": [
                            RawEntryData(
                                title="Target item",
                                url="https://example.com/target",
                                published_at=datetime.now(timezone.utc),
                                summary="Target summary",
                                content="Target full text",
                            )
                        ],
                        "used_rsshub_instance": "",
                    },
                )()

            import app.jobs
            app.jobs.run_attempt = fake_run_attempt
            settings = get_settings().model_copy(update={"llm_provider_type": "codex_cli", "codex_cli_path": "codex"})
            run = asyncio.run(fetch_source_job(db, "target", settings))
            assert run.summary_queued_count == 1
        print("ok")
        """,
        sqlite_url(tmp_path / "run-count.db"),
    )
    assert result.stdout.strip() == "ok"


def test_queue_auto_summaries_skips_items_with_active_summary_jobs(tmp_path: Path) -> None:
    result = run_python(
        """
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import func, select

        from app.config import get_settings
        from app.db import SessionLocal, init_db
        from app.models import Item, Job, JobStatus, Source, SummaryStatus
        from app.services import queue_auto_summaries
        from app.utils import dumps

        init_db()
        settings = get_settings().model_copy(update={"llm_provider_type": "codex_cli", "codex_cli_path": "codex"})
        with SessionLocal() as db:
            source = Source(
                id="target",
                name="Target",
                content_type="blog",
                platform="test",
                enabled=True,
                auto_summary_enabled=True,
            )
            db.add(source)
            db.flush()
            active_items = []
            now = datetime.now(timezone.utc)
            for index, status in enumerate([JobStatus.queued.value, JobStatus.running.value, JobStatus.retrying.value]):
                item = Item(
                    source_id="target",
                    canonical_url=f"https://example.com/active-{index}",
                    title=f"Active {index}",
                    url=f"https://example.com/active-{index}",
                    content_type="blog",
                    platform="test",
                    source_name="Target",
                    raw_text="Text ready for summary",
                    published_at=now + timedelta(minutes=index),
                    summary_status=SummaryStatus.pending.value,
                )
                db.add(item)
                db.flush()
                active_items.append(item.id)
                db.add(Job(type="summarize_item", status=status, payload=dumps({"item_id": item.id})))
            new_items = []
            for index in range(2):
                item = Item(
                    source_id="target",
                    canonical_url=f"https://example.com/new-{index}",
                    title=f"New {index}",
                    url=f"https://example.com/new-{index}",
                    content_type="blog",
                    platform="test",
                    source_name="Target",
                    raw_text="Text ready for summary",
                    published_at=now - timedelta(minutes=index + 1),
                    summary_status=SummaryStatus.skipped.value,
                )
                db.add(item)
                db.flush()
                new_items.append(item.id)
            db.commit()

            queued = queue_auto_summaries(db, settings, source_id="target", limit=2)
            assert queued == 2
            jobs = db.execute(select(Job).where(Job.type == "summarize_item")).scalars().all()
            assert len(jobs) == 5
            payloads = [dumps({"item_id": item_id}) for item_id in active_items]
            for payload in payloads:
                assert sum(1 for job in jobs if job.payload == payload) == 1
            for item_id in new_items:
                assert sum(1 for job in jobs if job.payload == dumps({"item_id": item_id})) == 1
            assert db.execute(select(func.count()).select_from(Job).where(Job.status == JobStatus.queued.value)).scalar_one() == 3
        print("ok")
        """,
        sqlite_url(tmp_path / "summary-active-jobs.db"),
    )
    assert result.stdout.strip() == "ok"


def test_health_exposes_job_queue_counts_targets_and_limits(tmp_path: Path) -> None:
    result = run_python(
        """
        from datetime import datetime, timedelta, timezone

        from fastapi.testclient import TestClient

        from app.api import app
        from app.db import SessionLocal, init_db
        from app.models import Fulltext, Item, Job, Source
        from app.utils import dumps

        init_db()
        now = datetime.now(timezone.utc)
        with SessionLocal() as db:
            source = Source(id="openai-news", name="OpenAI News", content_type="blog", platform="openai")
            db.merge(source)
            db.flush()
            item = Item(
                source_id="openai-news",
                canonical_url="https://example.com/item",
                title="A useful update",
                url="https://example.com/item",
                content_type="blog",
                platform="openai",
                source_name="OpenAI News",
                raw_text="Full text",
            )
            db.add(item)
            db.flush()
            db.add_all([
                Fulltext(item_id=item.id, extractor="feed_field", status="succeeded", text="Feed text"),
                Fulltext(item_id=item.id, extractor="generic_article", status="succeeded", text="Detail text"),
            ])

            jobs = [
                Job(
                    type="fetch_source",
                    status="running",
                    payload=dumps({"source_id": "openai-news"}),
                    attempts=1,
                    max_attempts=3,
                    scheduled_at=now - timedelta(minutes=10),
                    started_at=now - timedelta(minutes=9),
                ),
                Job(
                    type="summarize_item",
                    status="retrying",
                    payload=dumps({"item_id": item.id}),
                    attempts=2,
                    max_attempts=3,
                    scheduled_at=now - timedelta(minutes=8),
                    started_at=now - timedelta(minutes=7),
                    error_code="RateLimitError",
                    error_message="Provider asked us to retry.",
                ),
            ]
            for i in range(25):
                jobs.append(
                    Job(
                        type="fetch_source",
                        status="queued",
                        payload=dumps({"source_id": "openai-news"}),
                        scheduled_at=now + timedelta(seconds=i),
                    )
                )
            for i in range(12):
                jobs.append(
                    Job(
                        type="summarize_item",
                        status="failed" if i == 0 else "succeeded",
                        payload=dumps({"item_id": item.id}),
                        attempts=1,
                        max_attempts=3,
                        scheduled_at=now - timedelta(hours=2),
                        started_at=now - timedelta(hours=2),
                        finished_at=now - timedelta(minutes=i),
                        error_code="Boom" if i == 0 else "",
                        error_message="Failed loudly." if i == 0 else "",
                    )
                )
            db.add_all(jobs)
            db.commit()

        with TestClient(app) as client:
            response = client.get("/api/health")
            assert response.status_code == 200, response.text
        data = response.json()
        counts = data["jobs"]["counts"]
        assert counts["running"] == 1
        assert counts["retrying"] == 1
        assert counts["queued"] == 25
        assert counts["failed"] == 1
        assert counts["succeeded"] == 11
        assert counts["skipped"] == 0

        active = data["jobs"]["active"]
        recent = data["jobs"]["recent"]
        assert len(active) == 20
        assert len(recent) == 10
        assert active[0]["target"] == {"kind": "source", "id": "openai-news", "label": "OpenAI News"}
        summarize = next(job for job in active if job["type"] == "summarize_item")
        assert summarize["target"]["kind"] == "item"
        assert summarize["target"]["id"]
        assert summarize["target"]["label"] == "A useful update (OpenAI News)"
        source_health = next(source for source in data["sources"] if source["id"] == "openai-news")
        assert source_health["item_count"] == 1
        assert source_health["fulltext_success_count"] == 1
        assert source_health["fulltext_success_rate"] == 1
        assert recent[0]["status"] == "failed"
        assert recent[0]["error_code"] == "Boom"
        print("ok")
        """,
        sqlite_url(tmp_path / "health-jobs.db"),
    )
    assert result.stdout.strip() == "ok"


def test_builtin_sources_replace_arxiv_cs_cl_with_cs_se(tmp_path: Path) -> None:
    result = run_python(
        """
        from sqlalchemy import select

        from app.db import SessionLocal, init_db
        from app.catalog import ARXIV_CS_AI_API_URL, ARXIV_CS_SE_API_URL, DEFAULT_SOURCE_PACK_PATH
        from app.models import Source
        from app.services import load_source_pack, seed_builtin_sources
        from app.utils import loads

        init_db()
        with SessionLocal() as db:
            seed_builtin_sources(db)
            source_ids = set(db.execute(select(Source.id)).scalars())
            pack_ids = {source.id for source in load_source_pack(DEFAULT_SOURCE_PACK_PATH)}
            assert pack_ids <= source_ids
            assert "arxiv-cs-se" in source_ids
            assert "arxiv-cs-cl" not in source_ids
            source = db.get(Source, "arxiv-cs-se")
            assert source.name == "arXiv cs.SE"
            assert source.content_type == "paper"
            assert source.homepage_url == "https://arxiv.org/list/cs.SE/recent"
            assert loads(source.default_tags, []) == ["paper", "software-engineering"]
            assert source.attempts[0].url == ARXIV_CS_SE_API_URL
            assert "rss.arxiv.org/rss/cs.SE" not in source.attempts[0].url
            source = db.get(Source, "arxiv-cs-ai")
            assert source.name == "arXiv cs.AI"
            assert source.content_type == "paper"
            assert loads(source.default_tags, []) == ["paper", "ai"]
            assert source.attempts[0].url == ARXIV_CS_AI_API_URL
            assert "rss.arxiv.org/rss/cs.AI" not in source.attempts[0].url
        print("ok")
        """,
        sqlite_url(tmp_path / "builtin-cs-se.db"),
    )
    assert result.stdout.strip() == "ok"


def test_export_source_pack_strips_runtime_identity(tmp_path: Path) -> None:
    result = run_python(
        """
        import yaml

        from app.db import SessionLocal, init_db
        from app.services import export_source_pack, seed_builtin_sources

        init_db()
        with SessionLocal() as db:
            seed_builtin_sources(db)
            payload = yaml.safe_load(export_source_pack(db))
            assert payload["version"] == 1
            assert payload["sources"]
            assert all("is_builtin" not in source for source in payload["sources"])
            assert all("content_audit" not in source for source in payload["sources"])
            assert all("id" not in attempt for source in payload["sources"] for attempt in source["attempts"])
        print("ok")
        """,
        sqlite_url(tmp_path / "export-pack.db"),
    )
    assert result.stdout.strip() == "ok"


def test_builtin_arxiv_cs_se_legacy_attempt_syncs_to_api_url(tmp_path: Path) -> None:
    result = run_python(
        """
        from app.catalog import ARXIV_CS_SE_API_URL
        from app.db import SessionLocal, init_db
        from app.models import Source, SourceAttempt
        from app.services import seed_builtin_sources
        from app.utils import dumps

        init_db()
        with SessionLocal() as db:
            source = Source(
                id="arxiv-cs-se",
                name="arXiv cs.SE",
                content_type="paper",
                platform="arxiv",
                is_builtin=True,
                default_tags=dumps(["paper", "software-engineering"]),
            )
            source.attempts = [SourceAttempt(adapter="feed", url="https://rss.arxiv.org/rss/cs.SE")]
            db.add(source)
            db.commit()

            seed_builtin_sources(db)
            source = db.get(Source, "arxiv-cs-se")
            assert len(source.attempts) == 1
            assert source.attempts[0].adapter == "feed"
            assert source.attempts[0].url == ARXIV_CS_SE_API_URL
        print("ok")
        """,
        sqlite_url(tmp_path / "builtin-cs-se-sync.db"),
    )
    assert result.stdout.strip() == "ok"


def test_builtin_arxiv_cs_ai_legacy_attempt_syncs_to_api_url(tmp_path: Path) -> None:
    result = run_python(
        """
        from app.catalog import ARXIV_CS_AI_API_URL
        from app.db import SessionLocal, init_db
        from app.models import Source, SourceAttempt
        from app.services import seed_builtin_sources
        from app.utils import dumps

        init_db()
        with SessionLocal() as db:
            source = Source(
                id="arxiv-cs-ai",
                name="arXiv cs.AI",
                content_type="paper",
                platform="arxiv",
                is_builtin=True,
                default_tags=dumps(["paper", "ai"]),
            )
            source.attempts = [SourceAttempt(adapter="feed", url="https://rss.arxiv.org/rss/cs.AI")]
            db.add(source)
            db.commit()

            seed_builtin_sources(db)
            source = db.get(Source, "arxiv-cs-ai")
            assert len(source.attempts) == 1
            assert source.attempts[0].adapter == "feed"
            assert source.attempts[0].url == ARXIV_CS_AI_API_URL
        print("ok")
        """,
        sqlite_url(tmp_path / "builtin-cs-ai-sync.db"),
    )
    assert result.stdout.strip() == "ok"


def test_user_owned_arxiv_cs_se_attempt_is_not_overwritten(tmp_path: Path) -> None:
    result = run_python(
        """
        from app.catalog import ARXIV_CS_SE_API_URL
        from app.db import SessionLocal, init_db
        from app.models import Source, SourceAttempt
        from app.services import seed_builtin_sources

        custom_url = "https://example.com/my-cs-se.xml"

        init_db()
        with SessionLocal() as db:
            source = Source(
                id="arxiv-cs-se",
                name="My cs.SE",
                content_type="paper",
                platform="arxiv",
                is_builtin=False,
            )
            source.attempts = [SourceAttempt(adapter="feed", url=custom_url)]
            db.add(source)
            db.commit()

            seed_builtin_sources(db)
            source = db.get(Source, "arxiv-cs-se")
            assert source.name == "My cs.SE"
            assert source.is_builtin is False
            assert len(source.attempts) == 1
            assert source.attempts[0].url == custom_url
            assert source.attempts[0].url != ARXIV_CS_SE_API_URL
        print("ok")
        """,
        sqlite_url(tmp_path / "user-cs-se-preserve.db"),
    )
    assert result.stdout.strip() == "ok"


def test_retired_builtin_arxiv_cs_cl_cleanup_removes_related_data(tmp_path: Path) -> None:
    result = run_python(
        """
        from datetime import datetime, timezone

        from sqlalchemy import func, select

        from app.db import SessionLocal, init_db
        from app.models import Fulltext, Item, Job, RawEntry, Source, SourceAttempt, SourceRun, Summary
        from app.services import seed_builtin_sources
        from app.utils import dumps

        init_db()
        with SessionLocal() as db:
            source = Source(
                id="arxiv-cs-cl",
                name="arXiv cs.CL",
                content_type="paper",
                platform="arxiv",
                is_builtin=True,
                default_tags=dumps(["paper", "nlp"]),
            )
            source.attempts = [SourceAttempt(adapter="feed", url="https://rss.arxiv.org/rss/cs.CL")]
            db.add(source)
            db.flush()
            item = Item(
                source_id="arxiv-cs-cl",
                canonical_url="https://arxiv.org/abs/1",
                title="Retired paper",
                url="https://arxiv.org/abs/1",
                content_type="paper",
                platform="arxiv",
                source_name="arXiv cs.CL",
                raw_text="abstract",
            )
            db.add(item)
            db.flush()
            db.add_all([
                RawEntry(source_id="arxiv-cs-cl", entry_hash="hash", title="Raw", url="https://arxiv.org/abs/1"),
                SourceRun(source_id="arxiv-cs-cl", status="succeeded", finished_at=datetime.now(timezone.utc)),
                Fulltext(item_id=item.id, extractor="feed_field", text="abstract"),
                Summary(item_id=item.id, status="ready", data=dumps({"one_sentence": "done"})),
                Job(type="fetch_source", status="queued", payload=dumps({"source_id": "arxiv-cs-cl"})),
                Job(type="summarize_item", status="queued", payload=dumps({"item_id": item.id})),
            ])
            db.commit()

            seed_builtin_sources(db)
            assert db.get(Source, "arxiv-cs-cl") is None
            assert db.get(Source, "arxiv-cs-se") is not None
            for model in [Item, RawEntry, SourceRun, Fulltext, Summary, Job]:
                assert db.execute(select(func.count()).select_from(model)).scalar_one() == 0
        print("ok")
        """,
        sqlite_url(tmp_path / "retired-cleanup.db"),
    )
    assert result.stdout.strip() == "ok"


def test_retired_source_cleanup_preserves_user_owned_arxiv_cs_cl(tmp_path: Path) -> None:
    result = run_python(
        """
        from sqlalchemy import select

        from app.db import SessionLocal, init_db
        from app.models import Source
        from app.services import seed_builtin_sources

        init_db()
        with SessionLocal() as db:
            db.add(Source(id="arxiv-cs-cl", name="My cs.CL", content_type="paper", platform="arxiv", is_builtin=False))
            db.commit()
            seed_builtin_sources(db)
            source = db.get(Source, "arxiv-cs-cl")
            assert source is not None
            assert source.name == "My cs.CL"
            assert source.is_builtin is False
            source_ids = set(db.execute(select(Source.id)).scalars())
            assert "arxiv-cs-se" in source_ids
        print("ok")
        """,
        sqlite_url(tmp_path / "retired-user-source.db"),
    )
    assert result.stdout.strip() == "ok"


def test_settings_test_ai_none_provider_does_not_persist(tmp_path: Path) -> None:
    result = run_python(
        """
        from fastapi.testclient import TestClient
        from sqlalchemy import func, select

        from app.api import app
        from app.db import SessionLocal, init_db
        from app.models import Setting

        init_db()
        with TestClient(app) as client:
            response = client.post("/api/settings/test-ai", json={"llm_provider_type": "none"})
            assert response.status_code == 200, response.text
        data = response.json()
        assert data["ok"] is False
        assert data["provider"] == "none"
        assert "disabled" in data["error"]
        with SessionLocal() as db:
            assert db.execute(select(func.count()).select_from(Setting)).scalar_one() == 0
        print("ok")
        """,
        sqlite_url(tmp_path / "test-ai-none.db"),
    )
    assert result.stdout.strip() == "ok"


def test_rsshub_runtime_settings_are_config_file_authoritative(tmp_path: Path) -> None:
    result = run_python(
        """
        import os

        os.environ["RSSHUB_PUBLIC_INSTANCES"] = "https://env-rsshub-a.example,https://env-rsshub-b.example"
        os.environ["RSSHUB_SELF_HOSTED_BASE_URL"] = "https://env-self-hosted.example"

        from fastapi.testclient import TestClient

        from app.api import app
        from app.config import get_settings
        from app.db import SessionLocal, init_db
        from app.models import Setting
        from app.services import load_runtime_settings
        from app.utils import dumps

        get_settings.cache_clear()
        init_db()
        with SessionLocal() as db:
            db.add_all([
                Setting(key="rsshub_public_instances", value=dumps(["https://db-rsshub.example"])),
                Setting(key="rsshub_self_hosted_base_url", value=dumps("https://db-self-hosted.example")),
            ])
            db.commit()
            settings = load_runtime_settings(db)
            assert settings.rsshub_instances == [
                "https://env-self-hosted.example",
                "https://env-rsshub-a.example",
                "https://env-rsshub-b.example",
            ]
            assert "https://db-rsshub.example" not in settings.rsshub_instances
            assert "https://db-self-hosted.example" not in settings.rsshub_instances

        with TestClient(app) as client:
            settings_response = client.get("/api/settings")
            assert settings_response.status_code == 200, settings_response.text
            settings_data = settings_response.json()
            assert settings_data["rsshub_public_instances"] == [
                "https://env-rsshub-a.example",
                "https://env-rsshub-b.example",
            ]
            assert settings_data["rsshub_self_hosted_base_url"] == "https://env-self-hosted.example"
            response = client.patch(
                "/api/settings",
                json={
                    "rsshub_public_instances": ["https://patched-rsshub.example"],
                    "rsshub_self_hosted_base_url": "https://patched-self-hosted.example",
                    "llm_provider_type": "none",
                },
            )
            assert response.status_code == 200, response.text

        with SessionLocal() as db:
            settings = load_runtime_settings(db)
            assert "https://patched-rsshub.example" not in settings.rsshub_instances
            assert "https://patched-self-hosted.example" not in settings.rsshub_instances
            assert db.get(Setting, "rsshub_public_instances").value == dumps(["https://db-rsshub.example"])
            assert db.get(Setting, "rsshub_self_hosted_base_url").value == dumps("https://db-self-hosted.example")
        print("ok")
        """,
        sqlite_url(tmp_path / "rsshub-config-authority.db"),
    )
    assert result.stdout.strip() == "ok"


def test_settings_test_ai_codex_provider_uses_current_form_without_persisting(tmp_path: Path) -> None:
    result = run_python(
        """
        from fastapi.testclient import TestClient
        from sqlalchemy import func, select

        import app.api as api_module
        from app.api import app
        from app.db import SessionLocal, init_db
        from app.models import Setting

        seen = {}

        async def fake_summarize_codex_cli(_item, settings):
            seen["provider"] = settings.llm_provider_type
            seen["model"] = settings.codex_cli_model
            seen["path"] = settings.codex_cli_path
            return {"data": {"one_sentence": "ok"}, "usage": {}, "duration_ms": 12}

        api_module.summarize_codex_cli = fake_summarize_codex_cli

        init_db()
        with TestClient(app) as client:
            response = client.post(
                "/api/settings/test-ai",
                json={"llm_provider_type": "codex_cli", "codex_cli_path": "codex", "codex_cli_model": "gpt-test"},
            )
            assert response.status_code == 200, response.text
        data = response.json()
        assert data["ok"] is True
        assert data["provider"] == "codex_cli"
        assert data["model"] == "gpt-test"
        assert data["duration_ms"] == 12
        assert seen == {"provider": "codex_cli", "model": "gpt-test", "path": "codex"}
        with SessionLocal() as db:
            assert db.execute(select(func.count()).select_from(Setting)).scalar_one() == 0
        print("ok")
        """,
        sqlite_url(tmp_path / "test-ai-codex.db"),
    )
    assert result.stdout.strip() == "ok"


def test_settings_test_ai_custom_api_uses_saved_key_when_form_key_blank(tmp_path: Path) -> None:
    result = run_python(
        """
        from fastapi.testclient import TestClient
        from sqlalchemy import select

        import app.api as api_module
        from app.api import app
        from app.db import SessionLocal, init_db
        from app.models import Setting
        from app.utils import dumps

        seen = {}

        async def fake_summarize_openai_compatible(_item, settings):
            seen["base_url"] = settings.llm_base_url
            seen["api_key"] = settings.llm_api_key
            seen["model"] = settings.llm_model_name
            return {
                "data": {"one_sentence": "ok"},
                "usage": {"total_tokens": 3},
                "duration_ms": 34,
            }

        api_module.summarize_openai_compatible = fake_summarize_openai_compatible

        init_db()
        with SessionLocal() as db:
            db.add_all([
                Setting(key="llm_provider_type", value=dumps("openai_compatible")),
                Setting(key="llm_base_url", value=dumps("https://saved.example/v1")),
                Setting(key="llm_api_key", value=dumps("saved-key")),
                Setting(key="llm_model_name", value=dumps("saved-model")),
            ])
            db.commit()
        with TestClient(app) as client:
            response = client.post(
                "/api/settings/test-ai",
                json={
                    "llm_provider_type": "openai_compatible",
                    "llm_base_url": "https://form.example/v1",
                    "llm_api_key": "",
                    "llm_model_name": "form-model",
                },
            )
            assert response.status_code == 200, response.text
        data = response.json()
        assert data["ok"] is True
        assert data["provider"] == "openai_compatible"
        assert data["model"] == "form-model"
        assert data["usage"]["total_tokens"] == 3
        assert seen == {"base_url": "https://form.example/v1", "api_key": "saved-key", "model": "form-model"}
        with SessionLocal() as db:
            assert db.get(Setting, "llm_api_key").value == dumps("saved-key")
            assert db.execute(select(Setting).where(Setting.key == "llm_base_url")).scalar_one().value == dumps("https://saved.example/v1")
        print("ok")
        """,
        sqlite_url(tmp_path / "test-ai-custom-saved-key.db"),
    )
    assert result.stdout.strip() == "ok"


def test_settings_patch_preserves_blank_api_key_and_redacts_database_url(tmp_path: Path) -> None:
    result = run_python(
        """
        from fastapi.testclient import TestClient

        from app.api import app
        from app.db import SessionLocal, init_db
        from app.models import Setting
        from app.utils import dumps

        init_db()
        with SessionLocal() as db:
            db.add_all([
                Setting(key="llm_provider_type", value=dumps("openai_compatible")),
                Setting(key="llm_base_url", value=dumps("https://saved.example/v1")),
                Setting(key="llm_api_key", value=dumps("saved-key")),
                Setting(key="llm_model_name", value=dumps("saved-model")),
                Setting(key="database_url", value=dumps("postgresql://daily:secret-pass@db.example/daily_info")),
            ])
            db.commit()

        with TestClient(app) as client:
            settings = client.get("/api/settings")
            assert settings.status_code == 200, settings.text
            settings_data = settings.json()
            assert "llm_api_key" not in settings_data
            assert "secret-pass" not in settings.text
            assert settings_data["database_url"] == "postgresql://daily:***@db.example/daily_info"

            response = client.patch("/api/settings", json={"llm_api_key": "", "llm_model_name": "updated-model"})
            assert response.status_code == 200, response.text

        with SessionLocal() as db:
            assert db.get(Setting, "llm_api_key").value == dumps("saved-key")
            assert db.get(Setting, "llm_model_name").value == dumps("updated-model")
        print("ok")
        """,
        sqlite_url(tmp_path / "settings-secret-hygiene.db"),
    )
    assert result.stdout.strip() == "ok"


def test_settings_test_ai_custom_api_prefers_new_form_key_and_reports_incomplete_config(tmp_path: Path) -> None:
    result = run_python(
        """
        from fastapi.testclient import TestClient
        from sqlalchemy import func, select

        import app.api as api_module
        from app.api import app
        from app.db import SessionLocal, init_db
        from app.models import Setting

        seen = {}

        async def fake_summarize_openai_compatible(_item, settings):
            if not settings.llm_configured:
                raise RuntimeError("OpenAI-compatible provider is not fully configured")
            seen["api_key"] = settings.llm_api_key
            return {"data": {"one_sentence": "ok"}, "usage": {}, "duration_ms": 9}

        api_module.summarize_openai_compatible = fake_summarize_openai_compatible

        init_db()
        with TestClient(app) as client:
            response = client.post(
                "/api/settings/test-ai",
                json={
                    "llm_provider_type": "openai_compatible",
                    "llm_base_url": "https://form.example/v1",
                    "llm_api_key": "new-key",
                    "llm_model_name": "form-model",
                },
            )
            assert response.status_code == 200, response.text
            incomplete = client.post(
                "/api/settings/test-ai",
                json={"llm_provider_type": "openai_compatible", "llm_base_url": "", "llm_model_name": "form-model"},
            )
            assert incomplete.status_code == 200, incomplete.text
        assert response.json()["ok"] is True
        assert seen == {"api_key": "new-key"}
        incomplete_data = incomplete.json()
        assert incomplete_data["ok"] is False
        assert "not fully configured" in incomplete_data["error"]
        with SessionLocal() as db:
            assert db.execute(select(func.count()).select_from(Setting)).scalar_one() == 0
        print("ok")
        """,
        sqlite_url(tmp_path / "test-ai-custom-new-key.db"),
    )
    assert result.stdout.strip() == "ok"


def test_settings_patch_blank_api_key_preserves_saved_secret(tmp_path: Path) -> None:
    result = run_python(
        """
        from fastapi.testclient import TestClient

        from app.api import app
        from app.db import SessionLocal, init_db
        from app.models import Setting
        from app.utils import dumps

        init_db()
        with SessionLocal() as db:
            db.add_all([
                Setting(key="llm_provider_type", value=dumps("openai_compatible")),
                Setting(key="llm_base_url", value=dumps("https://saved.example/v1")),
                Setting(key="llm_api_key", value=dumps("saved-key")),
                Setting(key="llm_model_name", value=dumps("saved-model")),
            ])
            db.commit()

        with TestClient(app) as client:
            response = client.patch(
                "/api/settings",
                json={
                    "llm_provider_type": "openai_compatible",
                    "llm_base_url": "https://form.example/v1",
                    "llm_api_key": "",
                    "llm_model_name": "form-model",
                },
            )
            assert response.status_code == 200, response.text
            settings_response = client.get("/api/settings")
            assert settings_response.status_code == 200, settings_response.text

        settings_data = settings_response.json()
        assert "llm_api_key" not in settings_data
        assert settings_data["llm_configured"] is True
        assert settings_data["llm_base_url"] == "https://form.example/v1"
        assert settings_data["llm_model_name"] == "form-model"

        with SessionLocal() as db:
            assert db.get(Setting, "llm_api_key").value == dumps("saved-key")
        print("ok")
        """,
        sqlite_url(tmp_path / "settings-patch-blank-key.db"),
    )
    assert result.stdout.strip() == "ok"


def test_settings_patch_new_api_key_replaces_saved_secret(tmp_path: Path) -> None:
    result = run_python(
        """
        from fastapi.testclient import TestClient

        from app.api import app
        from app.db import SessionLocal, init_db
        from app.models import Setting
        from app.utils import dumps

        init_db()
        with SessionLocal() as db:
            db.add(Setting(key="llm_api_key", value=dumps("old-key")))
            db.commit()

        with TestClient(app) as client:
            response = client.patch("/api/settings", json={"llm_api_key": "new-key"})
            assert response.status_code == 200, response.text

        with SessionLocal() as db:
            assert db.get(Setting, "llm_api_key").value == dumps("new-key")
        print("ok")
        """,
        sqlite_url(tmp_path / "settings-patch-new-key.db"),
    )
    assert result.stdout.strip() == "ok"
