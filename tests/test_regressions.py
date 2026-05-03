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


def test_fetch_feed_parses_entry_categories_as_tags(tmp_path: Path) -> None:
    result = run_python(
        """
        import asyncio
        from unittest.mock import patch

        import httpx

        from app.adapters import fetch_feed

        atom = b'''<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <id>https://example.com/posts/1</id>
            <title>Useful Robotics Update</title>
            <updated>2026-04-23T15:29:09Z</updated>
            <link href="https://example.com/posts/1" rel="alternate" type="text/html"/>
            <summary>Robotics systems are improving.</summary>
            <category term="Robotics"/>
            <category term="Machine Learning"/>
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
            result = asyncio.run(fetch_feed("https://example.com/feed.xml"))

        assert result.entries[0].tags == ["Robotics", "Machine Learning"]
        print("ok")
        """,
        sqlite_url(tmp_path / "feed-tags.db"),
    )
    assert result.stdout.strip() == "ok"


def test_tag_sanitizer_filters_layout_class_noise() -> None:
    result = run_python(
        """
        from app.tags import sanitize_tags

        assert sanitize_tags(["px-0", "cols-12", "span-6", "start-4", "span-12", "mb-0"]) == []
        assert sanitize_tags(["Artificial Intelligence", "AI", "Machine Learning", "AI"]) == ["artificial-intelligence", "ai", "machine-learning"]
        print("ok")
        """,
        "sqlite:///:memory:",
    )
    assert result.stdout.strip() == "ok"


def test_settings_saves_multiple_llm_providers_without_exposing_keys(tmp_path: Path) -> None:
    result = run_python(
        """
        from fastapi.testclient import TestClient
        from sqlalchemy import select

        from app.api import app
        from app.db import SessionLocal, init_db
        from app.models import LLMProvider

        init_db()
        with TestClient(app) as client:
            response = client.patch("/api/settings", json={
                "llm_provider_type": "openai_compatible",
                "llm_providers": [
                    {
                        "name": "Primary",
                        "base_url": "https://primary.example.com/v1",
                        "api_key": "secret-1",
                        "model_name": "model-a",
                        "temperature": 0.2,
                        "timeout": 60,
                        "enabled": True,
                        "priority": 0,
                    },
                    {
                        "name": "Backup",
                        "base_url": "https://backup.example.com/v1",
                        "api_key": "secret-2",
                        "model_name": "model-b",
                        "temperature": 0.1,
                        "timeout": 30,
                        "enabled": False,
                        "priority": 1,
                    },
                ],
            })
            assert response.status_code == 200, response.text
            response = client.get("/api/settings")
            assert response.status_code == 200, response.text
            data = response.json()
            providers = data["llm_providers"]
            assert [provider["name"] for provider in providers] == ["Primary", "Backup"]
            assert providers[0]["has_api_key"] is True
            assert "api_key" not in providers[0]
            assert data["llm_model_name"] == "model-a"

            primary = providers[0]
            response = client.patch("/api/settings", json={
                "llm_provider_type": "openai_compatible",
                "llm_providers": [
                    {
                        "id": primary["id"],
                        "name": "Primary renamed",
                        "base_url": primary["base_url"],
                        "api_key": "",
                        "model_name": primary["model_name"],
                        "temperature": primary["temperature"],
                        "timeout": primary["timeout"],
                        "enabled": True,
                        "priority": 0,
                    }
                ],
            })
            assert response.status_code == 200, response.text

        with SessionLocal() as db:
            providers = db.execute(select(LLMProvider).order_by(LLMProvider.priority)).scalars().all()
            assert len(providers) == 1
            assert providers[0].name == "Primary renamed"
            assert providers[0].api_key == "secret-1"
        print("ok")
        """,
        sqlite_url(tmp_path / "settings-llm-providers.db"),
    )
    assert result.stdout.strip() == "ok"


def test_openai_summary_falls_back_to_next_enabled_provider(tmp_path: Path) -> None:
    result = run_python(
        """
        import asyncio

        from sqlalchemy import select

        from app.db import SessionLocal, init_db
        from app.jobs import summarize_item_job
        from app.models import Item, LLMProvider, Source, Summary, SummaryStatus
        from app.services import load_runtime_settings, set_setting_value

        init_db()
        with SessionLocal() as db:
            db.add(Source(id="source", name="Source", content_type="blog", platform="test"))
            item = Item(
                source_id="source",
                canonical_url="https://example.com/item",
                title="Item",
                url="https://example.com/item",
                content_type="blog",
                platform="test",
                source_name="Source",
                raw_text="Enough text for a summary.",
            )
            db.add(item)
            db.add_all([
                LLMProvider(name="Primary", provider_type="openai_compatible", base_url="https://primary.example.com/v1", api_key="secret-1", model_name="model-a", enabled=True, priority=0),
                LLMProvider(name="Backup", provider_type="openai_compatible", base_url="https://backup.example.com/v1", api_key="secret-2", model_name="model-b", enabled=True, priority=1),
            ])
            set_setting_value(db, "llm_provider_type", "openai_compatible")
            set_setting_value(db, "llm_providers_initialized", True)
            db.commit()

            async def fake_summarize(_item, settings):
                if settings.llm_model_name == "model-a":
                    raise RuntimeError("primary failed")
                return {
                    "data": {"one_sentence": "备用成功", "key_takeaways": ["要点"]},
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                    "duration_ms": 12,
                }

            import app.jobs
            app.jobs.summarize_openai_compatible = fake_summarize
            asyncio.run(summarize_item_job(db, item.id, load_runtime_settings(db)))
            summaries = db.execute(select(Summary).order_by(Summary.id)).scalars().all()
            db.refresh(item)
            assert item.summary_status == SummaryStatus.ready.value
            assert [summary.status for summary in summaries] == [SummaryStatus.failed.value, SummaryStatus.ready.value]
            assert [summary.model for summary in summaries] == ["model-a", "model-b"]
            assert summaries[0].error_message == "primary failed"
            assert item.summary == "备用成功"
        print("ok")
        """,
        sqlite_url(tmp_path / "summary-provider-fallback.db"),
    )
    assert result.stdout.strip() == "ok"


def test_openai_summary_marks_failed_when_all_providers_fail(tmp_path: Path) -> None:
    result = run_python(
        """
        import asyncio

        from sqlalchemy import select

        from app.db import SessionLocal, init_db
        from app.jobs import summarize_item_job
        from app.models import Item, LLMProvider, Source, Summary, SummaryStatus
        from app.services import load_runtime_settings, set_setting_value

        init_db()
        with SessionLocal() as db:
            db.add(Source(id="source", name="Source", content_type="blog", platform="test"))
            item = Item(
                source_id="source",
                canonical_url="https://example.com/item",
                title="Item",
                url="https://example.com/item",
                content_type="blog",
                platform="test",
                source_name="Source",
                raw_text="Enough text for a summary.",
            )
            db.add(item)
            db.add_all([
                LLMProvider(name="Primary", provider_type="openai_compatible", base_url="https://primary.example.com/v1", api_key="secret-1", model_name="model-a", enabled=True, priority=0),
                LLMProvider(name="Backup", provider_type="openai_compatible", base_url="https://backup.example.com/v1", api_key="secret-2", model_name="model-b", enabled=True, priority=1),
            ])
            set_setting_value(db, "llm_provider_type", "openai_compatible")
            set_setting_value(db, "llm_providers_initialized", True)
            db.commit()

            async def fake_summarize(_item, settings):
                raise RuntimeError(f"{settings.llm_model_name} failed")

            import app.jobs
            app.jobs.summarize_openai_compatible = fake_summarize
            asyncio.run(summarize_item_job(db, item.id, load_runtime_settings(db)))
            summaries = db.execute(select(Summary).order_by(Summary.id)).scalars().all()
            db.refresh(item)
            assert item.summary_status == SummaryStatus.failed.value
            assert [summary.status for summary in summaries] == [SummaryStatus.failed.value, SummaryStatus.failed.value]
            assert summaries[-1].error_message == "model-b failed"
        print("ok")
        """,
        sqlite_url(tmp_path / "summary-provider-all-failed.db"),
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
        from app.models import Item, Source, SourceAttempt, SourceSubscription, SummaryStatus
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
                tagging=dumps({"mode": "default", "max_tags": 5}),
            )
            target = Source(
                id="target",
                name="Target",
                content_type="blog",
                platform="test",
                enabled=True,
                auto_summary_enabled=True,
                fulltext=dumps({"strategy": "feed_field"}),
                tagging=dumps({"mode": "default", "max_tags": 5}),
            )
            target.attempts = [SourceAttempt(kind="direct", adapter="manual", enabled=True)]
            db.add_all([other, target])
            db.flush()
            db.add(SourceSubscription(source_id="target", subscribed=True))
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


def test_persist_entries_deduplicates_arxiv_across_categories(tmp_path: Path) -> None:
    result = run_python(
        """
        import asyncio
        from datetime import datetime, timezone

        from sqlalchemy import func, select

        from app.adapters import RawEntryData
        from app.config import get_settings
        from app.db import SessionLocal, init_db
        from app.models import Item, ItemSource, Source
        from app.services import persist_entries, query_items
        from app.utils import dumps, loads

        init_db()
        settings = get_settings()
        with SessionLocal() as db:
            ai = Source(id="arxiv-cs-ai", name="arXiv cs.AI", content_type="paper", platform="arxiv", default_tags=dumps(["paper", "ai"]))
            cl = Source(id="arxiv-cs-cl", name="arXiv cs.CL", content_type="paper", platform="arxiv", default_tags=dumps(["paper", "nlp"]))
            db.add_all([ai, cl])
            db.commit()

            first = RawEntryData(
                title="A Shared Paper",
                url="https://arxiv.org/abs/2604.21771v1",
                published_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
                summary="Short abstract",
                content="Short abstract",
                raw_payload={"id": "http://arxiv.org/abs/2604.21771v1"},
            )
            second = RawEntryData(
                title="A Shared Paper",
                url="https://arxiv.org/abs/2604.21771v2",
                published_at=datetime(2026, 4, 24, tzinfo=timezone.utc),
                summary="A longer abstract from another category.",
                content="A longer abstract from another category.",
                raw_payload={"id": "http://arxiv.org/abs/2604.21771v2"},
            )

            assert asyncio.run(persist_entries(db, ai, [first], settings))[1] == 1
            assert asyncio.run(persist_entries(db, cl, [second], settings))[1] == 0
            assert db.execute(select(func.count()).select_from(Item)).scalar_one() == 1
            item = db.execute(select(Item)).scalar_one()
            assert item.dedupe_key == "arxiv:2604.21771"
            assert loads(item.tags, []) == ["paper", "ai", "nlp"]
            sources = db.execute(select(ItemSource).order_by(ItemSource.source_id)).scalars().all()
            assert [source.source_id for source in sources] == ["arxiv-cs-ai", "arxiv-cs-cl"]

            filtered, total = query_items(db, source_id=["arxiv-cs-cl"], include_unsubscribed=True)
            assert total == 1
            assert filtered[0].id == item.id
        print("ok")
        """,
        sqlite_url(tmp_path / "dedupe-arxiv.db"),
    )
    assert result.stdout.strip() == "ok"


def test_persist_entries_deduplicates_tracking_urls_and_preserves_user_state(tmp_path: Path) -> None:
    result = run_python(
        """
        import asyncio
        from datetime import datetime, timezone

        from sqlalchemy import func, select

        from app.adapters import RawEntryData
        from app.config import get_settings
        from app.db import SessionLocal, init_db
        from app.models import Item, ItemSource, Source
        from app.services import item_to_out, persist_entries
        from app.utils import dumps

        init_db()
        settings = get_settings()
        with SessionLocal() as db:
            primary = Source(id="primary", name="Primary", content_type="blog", platform="example", default_tags=dumps(["primary"]))
            repost = Source(id="repost", name="Repost", content_type="blog", platform="example", default_tags=dumps(["repost"]))
            db.add_all([primary, repost])
            db.commit()

            first = RawEntryData(
                title="Launch Notes",
                url="https://example.com/news/launch?utm_source=newsletter&b=2&a=1",
                published_at=datetime(2026, 4, 20, tzinfo=timezone.utc),
                summary="Short",
                content="Short",
            )
            second = RawEntryData(
                title="Launch Notes",
                url="https://example.com/news/launch?a=1&b=2&utm_campaign=social",
                published_at=datetime(2026, 4, 21, tzinfo=timezone.utc),
                summary="Longer summary",
                content="Longer full text from the reposted feed.",
            )

            asyncio.run(persist_entries(db, primary, [first], settings))
            item = db.execute(select(Item)).scalar_one()
            item.read = True
            item.starred = True
            db.commit()

            assert asyncio.run(persist_entries(db, repost, [second], settings))[1] == 0
            assert db.execute(select(func.count()).select_from(Item)).scalar_one() == 1
            item = db.execute(select(Item)).scalar_one()
            assert item.read is True
            assert item.starred is True
            assert item.raw_text == "Longer full text from the reposted feed."
            assert db.execute(select(func.count()).select_from(ItemSource)).scalar_one() == 2
            data = item_to_out(item, db)
            assert [source.source_id for source in data.sources] == ["primary", "repost"]
        print("ok")
        """,
        sqlite_url(tmp_path / "dedupe-url-state.db"),
    )
    assert result.stdout.strip() == "ok"


def test_persist_entries_uses_feed_tagging_mode_and_filters_noise(tmp_path: Path) -> None:
    result = run_python(
        """
        import asyncio

        from sqlalchemy import select

        from app.adapters import RawEntryData
        from app.config import get_settings
        from app.db import SessionLocal, init_db
        from app.models import Item, ItemSource, Source
        from app.services import persist_entries
        from app.utils import dumps, loads

        init_db()
        with SessionLocal() as db:
            source = Source(
                id="feed-source",
                name="Feed Source",
                content_type="blog",
                platform="example",
                default_tags=dumps(["media"]),
                tagging=dumps({"mode": "feed", "max_tags": 5}),
            )
            db.add(source)
            db.commit()
            entry = RawEntryData(
                title="Robotics Update",
                url="https://example.com/robotics",
                summary="Robotics systems are improving.",
                content="Robotics systems are improving.",
                tags=["px-0", "Robotics", "Machine Learning", "span-6"],
            )
            asyncio.run(persist_entries(db, source, [entry], get_settings()))
            item = db.execute(select(Item)).scalar_one()
            item_source = db.execute(select(ItemSource)).scalar_one()
            assert loads(item.tags, []) == ["media", "robotics", "machine-learning"]
            assert loads(item_source.tags, []) == ["media", "robotics", "machine-learning"]
        print("ok")
        """,
        sqlite_url(tmp_path / "feed-tagging-mode.db"),
    )
    assert result.stdout.strip() == "ok"


def test_persist_entries_default_tagging_mode_ignores_feed_tags(tmp_path: Path) -> None:
    result = run_python(
        """
        import asyncio

        from sqlalchemy import select

        from app.adapters import RawEntryData
        from app.config import get_settings
        from app.db import SessionLocal, init_db
        from app.models import Item, Source
        from app.services import persist_entries
        from app.utils import dumps, loads

        init_db()
        with SessionLocal() as db:
            source = Source(
                id="default-source",
                name="Default Source",
                content_type="blog",
                platform="example",
                default_tags=dumps(["media", "span-6"]),
                tagging=dumps({"mode": "default", "max_tags": 5}),
            )
            db.add(source)
            db.commit()
            entry = RawEntryData(
                title="Ignored Feed Tags",
                url="https://example.com/default",
                summary="Text.",
                content="Text.",
                tags=["Robotics", "Machine Learning"],
            )
            asyncio.run(persist_entries(db, source, [entry], get_settings()))
            item = db.execute(select(Item)).scalar_one()
            assert loads(item.tags, []) == ["media"]
        print("ok")
        """,
        sqlite_url(tmp_path / "default-tagging-mode.db"),
    )
    assert result.stdout.strip() == "ok"


def test_persist_entries_llm_tagging_uses_generated_tags_and_falls_back(tmp_path: Path) -> None:
    result = run_python(
        """
        import asyncio

        from sqlalchemy import select

        from app.adapters import RawEntryData
        from app.config import get_settings
        from app.db import SessionLocal, init_db
        from app.models import Item, LLMUsageEvent, Source
        from app.services import llm_usage_stats, persist_entries
        from app.utils import dumps, loads

        init_db()
        settings = get_settings().model_copy(update={
            "llm_provider_type": "openai_compatible",
            "llm_base_url": "https://llm.example.com/v1",
            "llm_api_key": "secret",
            "llm_model_name": "tagger",
        })
        with SessionLocal() as db:
            source = Source(
                id="llm-source",
                name="LLM Source",
                content_type="blog",
                platform="example",
                default_tags=dumps(["media"]),
                tagging=dumps({"mode": "llm", "max_tags": 5}),
            )
            db.add(source)
            db.commit()

            calls = {"count": 0}

            async def fake_generate(_item, _settings, _max_tags):
                calls["count"] += 1
                return {
                    "tags": ["Artificial Intelligence", "span-6", "Robotics"],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14, "reasoning_tokens": 0, "raw": {"total_tokens": 14}},
                    "duration_ms": 25,
                }

            import app.services
            app.services.generate_tags_openai_compatible = fake_generate
            first = RawEntryData(
                title="AI Robotics",
                url="https://example.com/llm-ok",
                summary="Robotics and AI.",
                content="Robotics and AI.",
                tags=["px-0", "feed-tag"],
            )
            asyncio.run(persist_entries(db, source, [first], settings))
            item = db.execute(select(Item).where(Item.url == "https://example.com/llm-ok")).scalar_one()
            assert loads(item.tags, []) == ["media", "artificial-intelligence", "robotics"]
            assert calls["count"] == 1

            async def failing_generate(_item, _settings, _max_tags):
                calls["count"] += 1
                raise RuntimeError("provider failed")

            app.services.generate_tags_openai_compatible = failing_generate
            asyncio.run(persist_entries(db, source, [first], settings))
            item = db.execute(select(Item).where(Item.url == "https://example.com/llm-ok")).scalar_one()
            assert loads(item.tags, []) == ["media", "artificial-intelligence", "robotics"]
            assert calls["count"] == 1

            second = RawEntryData(
                title="Fallback Tags",
                url="https://example.com/llm-fail",
                summary="AI.",
                content="AI.",
                tags=["feed-tag"],
            )
            asyncio.run(persist_entries(db, source, [second], settings))
            item = db.execute(select(Item).where(Item.url == "https://example.com/llm-fail")).scalar_one()
            assert loads(item.tags, []) == ["media"]
            events = db.execute(select(LLMUsageEvent).order_by(LLMUsageEvent.id)).scalars().all()
            assert [event.status for event in events] == ["ready", "failed"]
            usage = llm_usage_stats(db)
            assert usage["all_time"]["requests"] == 2
            assert usage["all_time"]["success"] == 1
            assert usage["all_time"]["failed"] == 1
            assert usage["all_time"]["total_tokens"] == 14
            assert usage["last_error"] == "provider failed"
        print("ok")
        """,
        sqlite_url(tmp_path / "llm-tagging-mode.db"),
    )
    assert result.stdout.strip() == "ok"


def test_persist_entries_llm_tagging_caps_generation_per_fetch(tmp_path: Path) -> None:
    result = run_python(
        """
        import asyncio

        from sqlalchemy import select

        from app.adapters import RawEntryData
        from app.config import get_settings
        from app.db import SessionLocal, init_db
        from app.models import Item, LLMUsageEvent, Source
        from app.services import LLM_TAG_MAX_PER_FETCH, persist_entries
        from app.utils import dumps, loads

        init_db()
        settings = get_settings().model_copy(update={
            "llm_provider_type": "openai_compatible",
            "llm_base_url": "https://llm.example.com/v1",
            "llm_api_key": "secret",
            "llm_model_name": "tagger",
        })
        with SessionLocal() as db:
            source = Source(
                id="llm-cap-source",
                name="LLM Cap Source",
                content_type="blog",
                platform="example",
                default_tags=dumps(["media"]),
                tagging=dumps({"mode": "llm", "max_tags": 5}),
            )
            db.add(source)
            db.commit()

            calls = {"count": 0}

            async def fake_generate(item, _settings, _max_tags):
                calls["count"] += 1
                return {"tags": [f"generated-{calls['count']}"], "usage": {"total_tokens": 1}, "duration_ms": 1}

            import app.services
            app.services.generate_tags_openai_compatible = fake_generate
            entries = [
                RawEntryData(
                    title=f"Item {idx}",
                    url=f"https://example.com/item-{idx}",
                    summary="AI.",
                    content="AI.",
                    tags=["feed-tag"],
                )
                for idx in range(LLM_TAG_MAX_PER_FETCH + 3)
            ]
            asyncio.run(persist_entries(db, source, entries, settings))

            assert calls["count"] == LLM_TAG_MAX_PER_FETCH
            assert db.execute(select(LLMUsageEvent)).scalars().all()
            first = db.execute(select(Item).where(Item.url == "https://example.com/item-0")).scalar_one()
            capped = db.execute(select(Item).where(Item.url == f"https://example.com/item-{LLM_TAG_MAX_PER_FETCH}")).scalar_one()
            assert loads(first.tags, []) == ["media", "generated-1"]
            assert loads(capped.tags, []) == ["media"]
        print("ok")
        """,
        sqlite_url(tmp_path / "llm-tagging-cap.db"),
    )
    assert result.stdout.strip() == "ok"


def test_dedupe_handles_arxiv_old_style_ids_and_linkless_titles(tmp_path: Path) -> None:
    result = run_python(
        """
        import asyncio
        from datetime import datetime, timezone

        from sqlalchemy import func, select

        from app.adapters import RawEntryData
        from app.config import get_settings
        from app.db import SessionLocal, init_db
        from app.models import Item, Source
        from app.services import persist_entries

        init_db()
        settings = get_settings()
        with SessionLocal() as db:
            source = Source(id="math", name="Math", content_type="paper", platform="arxiv")
            db.add(source)
            db.commit()

            first = RawEntryData(title="Old-style Arxiv", url="https://arxiv.org/abs/math.CO/0309136v1", raw_payload={"id": "math.CO/0309136v1"})
            second = RawEntryData(title="Old-style Arxiv", url="https://arxiv.org/pdf/math.CO/0309136v2", raw_payload={"id": "math.CO/0309136v2"})
            assert asyncio.run(persist_entries(db, source, [first], settings))[1] == 1
            assert asyncio.run(persist_entries(db, source, [second], settings))[1] == 0

            same_title_a = RawEntryData(title="Untitled Linkless", url="", published_at=datetime(2026, 4, 1, tzinfo=timezone.utc))
            same_title_b = RawEntryData(title="Untitled Linkless", url="", published_at=datetime(2026, 4, 2, tzinfo=timezone.utc))
            assert asyncio.run(persist_entries(db, source, [same_title_a], settings))[1] == 1
            assert asyncio.run(persist_entries(db, source, [same_title_b], settings))[1] == 1
            assert db.execute(select(func.count()).select_from(Item)).scalar_one() == 3
        print("ok")
        """,
        sqlite_url(tmp_path / "dedupe-edge-cases.db"),
    )
    assert result.stdout.strip() == "ok"


def test_api_items_and_health_use_item_sources(tmp_path: Path) -> None:
    result = run_python(
        """
        from fastapi.testclient import TestClient

        from app.api import app
        from app.db import SessionLocal, init_db
        from app.models import Fulltext, Item, ItemSource, Source, SourceSubscription

        init_db()
        with SessionLocal() as db:
            db.add_all([
                Source(id="primary", name="Primary", content_type="blog", platform="origin"),
                Source(id="secondary", name="Secondary", content_type="blog", platform="mirror"),
                SourceSubscription(source_id="secondary", subscribed=True),
            ])
            db.flush()
            item = Item(source_id="primary", canonical_url="https://example.com/shared", title="Shared API Item", url="https://example.com/shared", content_type="blog", platform="origin", source_name="Primary", raw_text="Full text")
            db.add(item)
            db.flush()
            db.add_all([
                ItemSource(item_id=item.id, source_id="primary", source_name="Primary", url="https://example.com/shared", canonical_url="https://example.com/shared"),
                ItemSource(item_id=item.id, source_id="secondary", source_name="Secondary", url="https://mirror.example.com/shared", canonical_url="https://mirror.example.com/shared"),
                Fulltext(item_id=item.id, extractor="feed_field", status="succeeded", text="Full text"),
            ])
            db.commit()

        with TestClient(app) as client:
            response = client.get("/api/items?q=Shared%20API%20Item")
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["total"] == 1
            assert [source["source_id"] for source in data["items"][0]["sources"]] == ["primary", "secondary"]

            response = client.get("/api/items?platform=mirror")
            assert response.status_code == 200, response.text
            assert response.json()["total"] == 1

            health = client.get("/api/health")
            assert health.status_code == 200, health.text
            sources = {source["id"]: source for source in health.json()["sources"]}
            assert sources["primary"]["item_count"] == 1
            assert sources["secondary"]["item_count"] == 1
        print("ok")
        """,
        sqlite_url(tmp_path / "api-item-sources.db"),
    )
    assert result.stdout.strip() == "ok"


def test_cleanup_retired_source_keeps_items_with_remaining_sources(tmp_path: Path) -> None:
    result = run_python(
        """
        from sqlalchemy import func, select

        from app.db import SessionLocal, init_db
        from app.models import Item, ItemSource, Source
        from app.services import cleanup_retired_sources

        init_db()
        with SessionLocal() as db:
            retired = Source(id="retired", name="Retired", content_type="blog", platform="old", is_builtin=True)
            active = Source(id="active", name="Active", content_type="blog", platform="new")
            db.add_all([retired, active])
            db.flush()
            item = Item(source_id="retired", canonical_url="https://example.com/shared", title="Shared retained item", url="https://example.com/shared", content_type="blog", platform="old", source_name="Retired")
            db.add(item)
            db.flush()
            db.add_all([
                ItemSource(item_id=item.id, source_id="retired", source_name="Retired", url="https://example.com/shared", canonical_url="https://example.com/shared"),
                ItemSource(item_id=item.id, source_id="active", source_name="Active", url="https://active.example.com/shared", canonical_url="https://active.example.com/shared"),
            ])
            db.commit()

            cleanup_retired_sources(db, {"retired"})
            db.commit()

            assert db.get(Source, "retired") is None
            assert db.execute(select(func.count()).select_from(Item)).scalar_one() == 1
            item = db.execute(select(Item)).scalar_one()
            assert item.source_id == "active"
            assert item.source_name == "Active"
            assert db.execute(select(func.count()).select_from(ItemSource)).scalar_one() == 1
        print("ok")
        """,
        sqlite_url(tmp_path / "retired-source-shared-item.db"),
    )
    assert result.stdout.strip() == "ok"


def test_queue_auto_summaries_skips_items_with_active_summary_jobs(tmp_path: Path) -> None:
    result = run_python(
        """
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import func, select

        from app.config import get_settings
        from app.db import SessionLocal, init_db
        from app.models import Item, ItemSource, Job, JobStatus, Source, SourceSubscription, SummaryStatus
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
            db.add(SourceSubscription(source_id="target", subscribed=True))
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
                db.add(ItemSource(item_id=item.id, source_id="target", source_name="Target", url=item.url, canonical_url=item.canonical_url))
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
                db.add(ItemSource(item_id=item.id, source_id="target", source_name="Target", url=item.url, canonical_url=item.canonical_url))
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
        from app.models import Fulltext, Item, ItemSource, Job, Source, SourceSubscription
        from app.utils import dumps

        init_db()
        now = datetime.now(timezone.utc)
        with SessionLocal() as db:
            source = Source(id="openai-news", name="OpenAI News", content_type="blog", platform="openai")
            db.merge(source)
            db.flush()
            db.merge(SourceSubscription(source_id="openai-news", subscribed=True))
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
            db.add(ItemSource(item_id=item.id, source_id="openai-news", source_name="OpenAI News", url=item.url, canonical_url=item.canonical_url))
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


def test_builtin_sources_include_arxiv_api_categories(tmp_path: Path) -> None:
    result = run_python(
        """
        from sqlalchemy import select

        from app.db import SessionLocal, init_db
        from app.catalog import ARXIV_CS_AI_API_URL, ARXIV_CS_CL_API_URL, ARXIV_CS_SE_API_URL, DEFAULT_SOURCE_PACK_PATH
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
            assert "arxiv-cs-cl" in source_ids
            source = db.get(Source, "arxiv-cs-se")
            assert source.name == "arXiv cs.SE"
            assert source.content_type == "paper"
            assert source.homepage_url == "https://arxiv.org/list/cs.SE/recent"
            assert loads(source.default_tags, []) == ["paper", "software-engineering"]
            assert loads(source.tagging, {}) == {"mode": "feed", "max_tags": 5}
            assert source.attempts[0].url == ARXIV_CS_SE_API_URL
            assert "rss.arxiv.org/rss/cs.SE" not in source.attempts[0].url
            source = db.get(Source, "arxiv-cs-cl")
            assert source.name == "arXiv cs.CL"
            assert source.content_type == "paper"
            assert source.homepage_url == "https://arxiv.org/list/cs.CL/recent"
            assert loads(source.default_tags, []) == ["paper", "nlp"]
            assert loads(source.tagging, {}) == {"mode": "feed", "max_tags": 5}
            assert source.attempts[0].url == ARXIV_CS_CL_API_URL
            assert "rss.arxiv.org/rss/cs.CL" not in source.attempts[0].url
            source = db.get(Source, "arxiv-cs-ai")
            assert source.name == "arXiv cs.AI"
            assert source.content_type == "paper"
            assert loads(source.default_tags, []) == ["paper", "ai"]
            assert loads(source.tagging, {}) == {"mode": "feed", "max_tags": 5}
            assert source.attempts[0].url == ARXIV_CS_AI_API_URL
            assert "rss.arxiv.org/rss/cs.AI" not in source.attempts[0].url
        print("ok")
        """,
        sqlite_url(tmp_path / "builtin-cs-se.db"),
    )
    assert result.stdout.strip() == "ok"


def test_default_pack_adds_ai_and_tech_media_sources(tmp_path: Path) -> None:
    result = run_python(
        """
        from app.catalog import DEFAULT_SOURCE_PACK_PATH
        from app.services import load_source_pack

        sources = {source.id: source for source in load_source_pack(DEFAULT_SOURCE_PACK_PATH)}
        expected_blog_sources = {
            "google-ai-blog",
            "google-deepmind-blog",
            "nvidia-blog",
            "mit-technology-review-ai",
            "the-verge-tech",
            "techcrunch-ai",
            "ars-technica-ai",
            "wired-ai",
            "404-media",
        }
        assert expected_blog_sources <= set(sources)
        tech_media_sources = {
            "mit-technology-review-ai",
            "the-verge-tech",
            "techcrunch-ai",
            "ars-technica-ai",
            "wired-ai",
            "404-media",
        }
        for source_id in expected_blog_sources:
            source = sources[source_id]
            assert source.content_type == "blog"
            assert source.fulltext["strategy"] == "feed_or_detail"
            assert source.fulltext["min_feed_fulltext_chars"] == 1200
            assert source.fulltext["max_fulltext_per_run"] == 20
            assert source.attempts[0].adapter == "feed"
        assert {sources[source_id].group for source_id in tech_media_sources} == {"Tech Media"}
        print("ok")
        """,
        sqlite_url(tmp_path / "default-pack-tech-media.db"),
    )
    assert result.stdout.strip() == "ok"


def test_catalog_sources_are_opt_in_subscriptions(tmp_path: Path) -> None:
    result = run_python(
        """
        from sqlalchemy import func, select

        from app.db import SessionLocal, init_db
        from app.jobs import schedule_due_sources
        from app.models import Job, SourceSubscription
        from app.services import list_source_definitions, sync_default_source_pack
        from app.subscriptions import subscribe_source

        init_db()
        with SessionLocal() as db:
            sync_default_source_pack(db)
            definitions = list_source_definitions(db)
            assert len(definitions) >= 10
            assert {definition.id for definition in definitions} >= {"openai-news", "juya-ai-daily", "arxiv-cs-ai"}
            assert all(not definition.subscribed for definition in definitions)
            assert db.execute(select(func.count()).select_from(SourceSubscription)).scalar_one() == 0
            assert schedule_due_sources(db) == 0
            subscribe_source(db, "juya-ai-daily")
            definitions = {definition.id: definition for definition in list_source_definitions(db)}
            assert definitions["juya-ai-daily"].subscribed is True
            assert definitions["arxiv-cs-ai"].tagging.mode == "feed"
            assert definitions["arxiv-cs-ai"].tagging.max_tags == 5
            assert schedule_due_sources(db) == 1
            assert db.execute(select(func.count()).select_from(Job).where(Job.type == "fetch_source")).scalar_one() == 1
        print("ok")
        """,
        sqlite_url(tmp_path / "catalog-subscriptions.db"),
    )
    assert result.stdout.strip() == "ok"


def test_source_definitions_expose_latest_item_freshness(tmp_path: Path) -> None:
    result = run_python(
        """
        from datetime import datetime, timezone

        from app.db import SessionLocal, init_db
        from app.models import Item, ItemSource, Source, SourceAttempt, SourceRun
        from app.services import list_source_definitions

        init_db()
        with SessionLocal() as db:
            db.add_all([
                Source(id="primary", name="Primary", content_type="blog", platform="origin"),
                Source(id="secondary", name="Secondary", content_type="blog", platform="mirror"),
            ])
            db.flush()
            db.add_all([
                SourceAttempt(source_id="primary", adapter="feed", url="https://example.com/feed.xml"),
                SourceAttempt(source_id="secondary", adapter="feed", url="https://mirror.example.com/feed.xml"),
                SourceRun(source_id="primary", status="succeeded", raw_count=10, item_count=1),
                SourceRun(source_id="secondary", status="succeeded", raw_count=10, item_count=0),
            ])
            older = Item(
                source_id="primary",
                canonical_url="https://example.com/old",
                title="Older item",
                url="https://example.com/old",
                content_type="blog",
                platform="origin",
                source_name="Primary",
                published_at=datetime(2026, 4, 30, 8, tzinfo=timezone.utc),
                created_at=datetime(2026, 4, 30, 9, tzinfo=timezone.utc),
            )
            shared = Item(
                source_id="primary",
                canonical_url="https://example.com/shared",
                title="Shared latest item",
                url="https://example.com/shared",
                content_type="blog",
                platform="origin",
                source_name="Primary",
                published_at=datetime(2026, 5, 2, 8, tzinfo=timezone.utc),
                created_at=datetime(2026, 5, 2, 9, tzinfo=timezone.utc),
            )
            db.add_all([older, shared])
            db.flush()
            db.add_all([
                ItemSource(item_id=older.id, source_id="primary", source_name="Primary", url=older.url, canonical_url=older.canonical_url),
                ItemSource(item_id=shared.id, source_id="primary", source_name="Primary", url=shared.url, canonical_url=shared.canonical_url),
                ItemSource(item_id=shared.id, source_id="secondary", source_name="Secondary", url="https://mirror.example.com/shared", canonical_url="https://mirror.example.com/shared"),
            ])
            db.commit()

            definitions = {definition.id: definition for definition in list_source_definitions(db)}
            def as_utc(value):
                return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

            assert definitions["primary"].latest_item_title == "Shared latest item"
            assert as_utc(definitions["primary"].latest_item_published_at) == datetime(2026, 5, 2, 8, tzinfo=timezone.utc)
            assert as_utc(definitions["primary"].latest_item_ingested_at) == datetime(2026, 5, 2, 9, tzinfo=timezone.utc)
            assert definitions["secondary"].latest_item_title == "Shared latest item"
            assert as_utc(definitions["secondary"].latest_item_published_at) == datetime(2026, 5, 2, 8, tzinfo=timezone.utc)
            assert as_utc(definitions["secondary"].latest_item_ingested_at) == datetime(2026, 5, 2, 9, tzinfo=timezone.utc)
        print("ok")
        """,
        sqlite_url(tmp_path / "source-definition-freshness.db"),
    )
    assert result.stdout.strip() == "ok"


def test_source_definition_patch_writes_yaml_and_syncs_database(tmp_path: Path) -> None:
    catalog_dir = tmp_path / "catalog"
    source_yaml = """
schema_version: 1
sources:
  - id: editable
    title: Editable Source
    kind: blog
    platform: test
    homepage: https://example.com
    group: Test
    language: en
    tags: [old]
    tagging:
      mode: default
      max_tags: 5
    fetch:
      strategy: first_success
      interval_seconds: 3600
      attempts:
        - adapter: feed
          url: https://example.com/feed.xml
    fulltext:
      mode: feed_only
      min_feed_chars: 1200
      max_detail_pages_per_run: 0
    summary:
      auto: false
      window_days: 7
    auth:
      mode: none
""".lstrip()
    result = run_python(
        f"""
        from pathlib import Path

        import yaml
        from fastapi.testclient import TestClient

        import app.source_catalog as source_catalog

        source_catalog.SOURCE_CATALOG_DIR = Path({str(catalog_dir)!r})
        source_catalog.SOURCE_CATALOG_DIR.mkdir(parents=True, exist_ok=True)
        source_file = source_catalog.SOURCE_CATALOG_DIR / "test.yaml"
        source_file.write_text({source_yaml!r}, encoding="utf-8")

        from app.api import app
        from app.db import SessionLocal
        from app.models import Source
        from app.utils import loads

        with TestClient(app) as client:
            response = client.patch("/api/source-definitions/editable", json={{
                "summary": {{"auto": True, "window_days": 3}},
                "fetch": {{"interval_seconds": 7200}},
                "fulltext": {{"mode": "feed_then_detail", "min_feed_chars": 800, "max_detail_pages_per_run": 5}},
                "tagging": {{"mode": "llm", "max_tags": 6}},
                "tags": ["new", "ai"],
                "filters": {{"include_keywords": ["research"], "exclude_keywords": ["sponsored"]}},
                "group": "Updated",
                "priority": 42,
                "language": "zh-CN",
            }})
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["summary"] == {{"auto": True, "window_days": 3}}
            assert data["auto_summary_enabled"] is True
            assert data["auto_summary_days"] == 3
            assert data["fetch"]["interval_seconds"] == 7200

            refreshed = client.get("/api/source-definitions")
            assert refreshed.status_code == 200, refreshed.text
            row = next(source for source in refreshed.json() if source["id"] == "editable")
            assert row["summary"] == {{"auto": True, "window_days": 3}}

        payload = yaml.safe_load(source_file.read_text(encoding="utf-8"))
        raw = payload["sources"][0]
        assert raw["summary"] == {{"auto": True, "window_days": 3}}
        assert raw["fetch"]["interval_seconds"] == 7200
        assert raw["fulltext"]["mode"] == "feed_then_detail"
        assert raw["tagging"] == {{"mode": "llm", "max_tags": 6}}
        assert raw["tags"] == ["new", "ai"]
        assert raw["filters"] == {{"include_keywords": ["research"], "exclude_keywords": ["sponsored"]}}

        with SessionLocal() as db:
            source = db.get(Source, "editable")
            assert source.auto_summary_enabled is True
            assert source.auto_summary_days == 3
            assert source.poll_interval == 7200
            assert source.group == "Updated"
            assert source.priority == 42
            assert source.language_hint == "zh-CN"
            assert loads(source.spec_json, {{}})["summary"] == {{"auto": True, "window_days": 3}}
            assert source.spec_hash
            assert source.catalog_file == "test.yaml"
        print("ok")
        """,
        sqlite_url(tmp_path / "source-definition-patch.db"),
    )
    assert result.stdout.strip() == "ok"


def test_source_definition_patch_rejects_invalid_values_without_writing_yaml(tmp_path: Path) -> None:
    catalog_dir = tmp_path / "catalog"
    source_yaml = """
schema_version: 1
sources:
  - id: invalid-window
    title: Invalid Window
    kind: blog
    platform: test
    homepage: https://example.com
    group: Test
    language: en
    tags: [old]
    fetch:
      strategy: first_success
      interval_seconds: 3600
      attempts:
        - adapter: feed
          url: https://example.com/feed.xml
    fulltext:
      mode: feed_only
    summary:
      auto: false
      window_days: 7
    auth:
      mode: none
""".lstrip()
    result = run_python(
        f"""
        from pathlib import Path

        import yaml
        from fastapi.testclient import TestClient

        import app.source_catalog as source_catalog

        source_catalog.SOURCE_CATALOG_DIR = Path({str(catalog_dir)!r})
        source_catalog.SOURCE_CATALOG_DIR.mkdir(parents=True, exist_ok=True)
        source_file = source_catalog.SOURCE_CATALOG_DIR / "test.yaml"
        source_file.write_text({source_yaml!r}, encoding="utf-8")
        original = source_file.read_text(encoding="utf-8")

        from app.api import app
        from app.db import SessionLocal
        from app.models import Source

        with TestClient(app) as client:
            response = client.patch("/api/source-definitions/invalid-window", json={{"summary": {{"auto": True, "window_days": 0}}}})
            assert response.status_code == 422, response.text

        assert source_file.read_text(encoding="utf-8") == original
        with SessionLocal() as db:
            source = db.get(Source, "invalid-window")
            assert source.auto_summary_enabled is False
            assert source.auto_summary_days == 7
        print("ok")
        """,
        sqlite_url(tmp_path / "source-definition-invalid-patch.db"),
    )
    assert result.stdout.strip() == "ok"


def test_create_source_definition_writes_custom_yaml(tmp_path: Path) -> None:
    catalog_dir = tmp_path / "catalog"
    result = run_python(
        f"""
        from pathlib import Path

        import yaml
        from fastapi.testclient import TestClient

        import app.source_catalog as source_catalog

        source_catalog.SOURCE_CATALOG_DIR = Path({str(catalog_dir)!r})
        source_catalog.SOURCE_CATALOG_DIR.mkdir(parents=True, exist_ok=True)

        from app.api import app
        from app.db import SessionLocal
        from app.models import Source, SourceSubscription

        payload = {{
            "id": "custom-web",
            "title": "Custom Web",
            "kind": "blog",
            "platform": "custom",
            "homepage": "https://example.com",
            "language": "en",
            "tags": ["custom"],
            "group": "Custom",
            "fetch": {{
                "strategy": "first_success",
                "interval_seconds": 3600,
                "attempts": [{{"adapter": "feed", "url": "https://example.com/feed.xml"}}],
            }},
            "fulltext": {{"mode": "feed_only"}},
            "summary": {{"auto": True, "window_days": 5}},
            "auth": {{"mode": "none"}},
        }}

        with TestClient(app) as client:
            response = client.post("/api/source-definitions", json=payload)
            assert response.status_code == 200, response.text
            assert response.json()["catalog_file"] == "custom.yaml"

        custom_file = source_catalog.SOURCE_CATALOG_DIR / "custom.yaml"
        data = yaml.safe_load(custom_file.read_text(encoding="utf-8"))
        assert data["schema_version"] == 1
        assert [source["id"] for source in data["sources"]] == ["custom-web"]
        assert data["sources"][0]["summary"] == {{"auto": True, "window_days": 5}}

        with SessionLocal() as db:
            source = db.get(Source, "custom-web")
            assert source.catalog_file == "custom.yaml"
            subscription = db.get(SourceSubscription, "custom-web")
            assert subscription and subscription.subscribed
        print("ok")
        """,
        sqlite_url(tmp_path / "source-definition-custom-yaml.db"),
    )
    assert result.stdout.strip() == "ok"


def test_feed_defaults_to_subscribed_sources(tmp_path: Path) -> None:
    result = run_python(
        """
        from app.db import SessionLocal, init_db
        from app.models import Item, ItemSource, Source, SourceSubscription
        from app.services import query_items

        init_db()
        with SessionLocal() as db:
            db.add_all([
                Source(id="subscribed", name="Subscribed", content_type="blog", platform="test"),
                Source(id="available", name="Available", content_type="blog", platform="test"),
                SourceSubscription(source_id="subscribed", subscribed=True),
            ])
            db.flush()
            subscribed_item = Item(source_id="subscribed", canonical_url="https://example.com/sub", title="Subscribed item", url="https://example.com/sub", content_type="blog", platform="test", source_name="Subscribed")
            available_item = Item(source_id="available", canonical_url="https://example.com/available", title="Available item", url="https://example.com/available", content_type="blog", platform="test", source_name="Available")
            db.add_all([subscribed_item, available_item])
            db.flush()
            db.add_all([
                ItemSource(item_id=subscribed_item.id, source_id="subscribed", source_name="Subscribed", url=subscribed_item.url, canonical_url=subscribed_item.canonical_url),
                ItemSource(item_id=available_item.id, source_id="available", source_name="Available", url=available_item.url, canonical_url=available_item.canonical_url),
            ])
            db.commit()
            items, total = query_items(db)
            assert total == 1
            assert items[0].source_id == "subscribed"
            items, total = query_items(db, since="today")
            assert total == 1
            assert items[0].source_id == "subscribed"
            items, total = query_items(db, include_unsubscribed=True)
            assert total == 2
        print("ok")
        """,
        sqlite_url(tmp_path / "feed-subscriptions.db"),
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


def test_html_index_adapter_uses_timeout_config(tmp_path: Path) -> None:
    result = run_python(
        """
        import asyncio

        from app.adapters import AdapterResult, HtmlIndexAdapter
        import app.adapters as adapters
        from app.models import SourceAttempt
        from app.utils import dumps

        seen = {}

        async def fake_fetch_html_index(url: str, timeout: int = 20) -> AdapterResult:
            seen["url"] = url
            seen["timeout"] = timeout
            return AdapterResult(entries=[])

        adapters.fetch_html_index = fake_fetch_html_index
        attempt = SourceAttempt(adapter="html_index", url="https://example.com", config=dumps({"timeout_seconds": 7}))

        asyncio.run(HtmlIndexAdapter().fetch(attempt, settings=None))

        assert seen == {"url": "https://example.com", "timeout": 7}
        print("ok")
        """,
        sqlite_url(tmp_path / "html-index-timeout.db"),
    )
    assert result.stdout.strip() == "ok"


def test_page_index_adapter_parses_reader_fallback_dates(tmp_path: Path) -> None:
    result = run_python(
        """
        import asyncio
        from unittest.mock import patch

        import httpx

        from app.adapters import PageIndexAdapter
        from app.models import SourceAttempt
        from app.utils import dumps

        markdown = '''
        [A new class of intelligence for real work Release Apr 23, 2026 12 min read](https://openai.com/index/introducing-gpt-5-5/)
        [Our most capable and efficient frontier model for professional work Release Mar 5, 2026 16 min read](https://openai.com/index/introducing-gpt-5-4/)
        '''

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def get(self, url, headers=None):
                request = httpx.Request("GET", url)
                if url == "https://openai.com/research/":
                    return httpx.Response(403, text="blocked", request=request)
                if url.startswith("https://r.jina.ai/"):
                    return httpx.Response(200, text=markdown, request=request)
                raise AssertionError(url)

        attempt = SourceAttempt(
            adapter="page_index",
            url="https://openai.com/research/",
            config=dumps({"reader_fallback": True, "limit": 3}),
        )

        with patch("app.adapters.httpx.AsyncClient", FakeAsyncClient):
            result = asyncio.run(PageIndexAdapter().fetch(attempt, settings=None))

        assert result.entries[0].title == "A new class of intelligence for real work"
        assert result.entries[0].url == "https://openai.com/index/introducing-gpt-5-5/"
        assert result.entries[0].published_at.isoformat().startswith("2026-04-23")
        assert result.warnings == ["Used reader fallback for blocked index page."]
        print("ok")
        """,
        sqlite_url(tmp_path / "page-index-reader.db"),
    )
    assert result.stdout.strip() == "ok"


def test_page_index_adapter_fills_anthropic_featured_date_from_detail(tmp_path: Path) -> None:
    result = run_python(
        """
        import asyncio
        from unittest.mock import patch

        import httpx

        from app.adapters import PageIndexAdapter
        from app.models import SourceAttempt
        from app.utils import dumps

        index_html = '''
        <main>
          <a href="/engineering/april-23-postmortem">Featured An update on recent Claude Code quality reports We traced recent reports.</a>
          <a href="/engineering/managed-agents">Scaling Managed Agents: Decoupling the brain from the hands Apr 08, 2026</a>
        </main>
        '''
        detail_html = '''
        <main>
          <h1>An update on recent Claude Code quality reports</h1>
          <p>Published Apr 23, 2026</p>
        </main>
        '''

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def get(self, url, headers=None):
                request = httpx.Request("GET", url)
                if url == "https://www.anthropic.com/engineering":
                    return httpx.Response(200, text=index_html, request=request)
                if url == "https://www.anthropic.com/engineering/april-23-postmortem":
                    return httpx.Response(200, text=detail_html, request=request)
                raise AssertionError(url)

        attempt = SourceAttempt(
            adapter="page_index",
            url="https://www.anthropic.com/engineering",
            config=dumps({"limit": 5}),
        )

        with patch("app.adapters.httpx.AsyncClient", FakeAsyncClient):
            result = asyncio.run(PageIndexAdapter().fetch(attempt, settings=None))

        assert result.entries[0].title == "An update on recent Claude Code quality reports"
        assert result.entries[0].published_at.isoformat().startswith("2026-04-23")
        assert result.entries[1].title == "Scaling Managed Agents: Decoupling the brain from the hands"
        assert result.entries[1].published_at.isoformat().startswith("2026-04-08")
        print("ok")
        """,
        sqlite_url(tmp_path / "page-index-anthropic.db"),
    )
    assert result.stdout.strip() == "ok"


def test_user_owned_arxiv_cs_cl_attempt_is_not_overwritten(tmp_path: Path) -> None:
    result = run_python(
        """
        from sqlalchemy import select

        from app.catalog import ARXIV_CS_CL_API_URL
        from app.db import SessionLocal, init_db
        from app.models import Source, SourceAttempt
        from app.services import seed_builtin_sources

        custom_url = "https://example.com/my-cs-cl.xml"

        init_db()
        with SessionLocal() as db:
            source = Source(id="arxiv-cs-cl", name="My cs.CL", content_type="paper", platform="arxiv", is_builtin=False)
            source.attempts = [SourceAttempt(adapter="feed", url=custom_url)]
            db.add(source)
            db.commit()

            seed_builtin_sources(db)
            source = db.get(Source, "arxiv-cs-cl")
            assert source is not None
            assert source.name == "My cs.CL"
            assert source.is_builtin is False
            assert len(source.attempts) == 1
            assert source.attempts[0].url == custom_url
            assert source.attempts[0].url != ARXIV_CS_CL_API_URL
            source_ids = set(db.execute(select(Source.id)).scalars())
            assert "arxiv-cs-se" in source_ids
        print("ok")
        """,
        sqlite_url(tmp_path / "user-cs-cl-preserve.db"),
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
