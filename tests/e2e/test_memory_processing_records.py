# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Durable Source processing results are readable after a no-write and restart."""

import asyncio
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import time_machine
from sqlalchemy.exc import IntegrityError

from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryCandidate,
    AtomicMemoryExtractionOutput,
    AtomicMemoryGenerationPipeline,
)
from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemoryContent
from powercontext.builtin.artifacts.atomic_memory.reconciliation import (
    AtomicMemoryReconciliationContent,
    AtomicMemoryReconciliationOutput,
)
from powercontext.builtin.inference import GenerationResult, InferenceUnavailableError, character_token_estimator
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, RuntimeConfig
from powercontext.builtin.runtime.composition import open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft
from powercontext.server.app import create_app
from powercontext.server.dashboard.routes import router
from tests.e2e.dream_support import memory_source_text

TEXT = "The deployment region is Shanghai."
REASON = "The existing memory already states the same deployment region."


class Extractor:
    async def generate(self, request, /):
        return GenerationResult(
            output=AtomicMemoryExtractionOutput(
                candidates=tuple(
                    AtomicMemoryCandidate(
                        kind="fact",
                        text=TEXT,
                        description="Deployment region fact.",
                        evidence_ids=(item.evidence_id,),
                    )
                    for item in request.evidence
                    if memory_source_text(item) != "No candidate."
                )
            )
        )


class DuplicateReconciler:
    async def generate(self, request, /):
        if not request.related:
            return GenerationResult(
                output=AtomicMemoryReconciliationOutput(
                    action="create",
                    compared_ids=(),
                    content=AtomicMemoryReconciliationContent(
                        kind="fact", text=TEXT, description="Deployment region fact."
                    ),
                    evidence_ids=request.proposal.evidence_ids,
                    reason="Keep the new deployment region.",
                )
            )
        return GenerationResult(
            output=AtomicMemoryReconciliationOutput(
                action="noop",
                compared_ids=tuple(item.item_id for item in request.related),
                target_ids=(request.related[0].item_id,),
                reason=REASON,
            )
        )


def plain(html):
    """Compare rendered text without depending on template tag placement."""
    return re.sub(r"<[^>]+>", "", html)


def pipeline():
    return AtomicMemoryGenerationPipeline(
        extractor=Extractor(),
        reconciler=DuplicateReconciler(),
        estimator=character_token_estimator(),
    )


def app_for(runtime):
    app = create_app(application=runtime)
    app.include_router(router, prefix="/dashboard")
    return app


def test_duplicate_source_keeps_reason_and_exact_retained_memory_after_restart(tmp_path):
    config = BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/processing.db"),
        runtime=RuntimeConfig(atomic_memory_related_mode="fts"),
    )

    async def scenario():
        async with open_builtin_runtime(config, candidate_pipeline=pipeline()) as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            scope = await runtime.scopes.create(ScopeDraft(title="Processing", summary="Results", idempotency_key="p"))
            memory = runtime.atomic_memory.for_scope(scope.scope_id)
            sources = runtime.sources.for_scope(scope.scope_id)
            await sources.capture(CaptureSource(source_id="first", content=TEXT, metadata={}))
            await memory.flush(limit=1)
            (original,) = (await memory.list()).items
            await sources.capture(CaptureSource(source_id="duplicate", content=TEXT, metadata={}))
            await memory.flush(limit=1)
            assert [(item.ref.artifact_id, item.ref.revision) for item in (await memory.list()).items] == [
                (original.ref.artifact_id, 1)
            ]
            path = f"/v1/scopes/{scope.scope_id}/sources/content/duplicate/processing"
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app_for(runtime)), base_url="http://test"
            ) as client:
                response = await client.get(path)
                assert response.status_code == 200, response.text
                record = response.json()["items"][0]
                assert record["status"] == "succeeded"
                assert record["outcome"] == "no_change"
                assert record["results"] == []
                assert record["decisions"][0]["reason"] == REASON
                assert record["retained_artifacts"] == [original.ref.model_dump(mode="json")]
                page = await client.get(
                    "/dashboard/processing", params={"scope": scope.scope_id, "source_id": "duplicate", "lang": "en"}
                )
                assert page.status_code == 200, page.text
                assert REASON in page.text
                assert original.ref.artifact_id in page.text
        async with (
            open_builtin_runtime(config, candidate_pipeline=pipeline()) as reopened,
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(reopened)), base_url="http://test") as client,
        ):
            response = await client.get(path)
            assert response.status_code == 200, response.text
            assert response.json()["items"][0] == record

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["extraction", "commit"])
def test_failed_attempt_and_retry_keep_rollback_safe_history(tmp_path, fault):
    class RecoverableExtractor(Extractor):
        broken = fault == "extraction"

        async def generate(self, request, /):
            if self.broken:
                raise RuntimeError("private-provider-error-must-not-be-disclosed")
            return await super().generate(request)

    async def scenario():
        selected = pipeline()
        extractor = RecoverableExtractor()
        selected = AtomicMemoryGenerationPipeline(
            extractor=extractor,
            reconciler=DuplicateReconciler(),
            estimator=character_token_estimator(),
        )
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/fault.db"))
        async with open_builtin_runtime(config, candidate_pipeline=selected) as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            scope = await runtime.scopes.create(ScopeDraft(title="Failures", summary="Results", idempotency_key="f"))
            await runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(source_id="input", content=TEXT, metadata={})
            )
            memory = runtime.atomic_memory.for_scope(scope.scope_id)
            if fault == "commit":
                async with runtime.primary_database.transaction() as connection:
                    await connection.exec_driver_sql(
                        "CREATE TRIGGER reject_processing_success BEFORE UPDATE ON pc_memory_processing_records "
                        "WHEN json_extract(CAST(NEW.payload AS TEXT),'$.status') = 'succeeded' "
                        "BEGIN SELECT RAISE(ABORT, 'fixture publication failure'); END"
                    )
            with pytest.raises(IntegrityError if fault == "commit" else RuntimeError):
                await memory.flush(limit=1)
            assert (await memory.list()).items == ()
            assert (await memory.cursor()).sequence == 0
            app = app_for(runtime)
            app.state.dashboard_runtime = runtime
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                path = f"/v1/scopes/{scope.scope_id}/sources/content/input/processing"
                failed = (await client.get(path)).json()["items"][0]
                assert failed["status"] == "failed" and failed["results"] == []
                assert failed["stage"] == ("commit" if fault == "commit" else "extract_compare")
                attempts = await client.get(
                    "/dashboard/processing",
                    params={"scope": scope.scope_id, "record_type": "attempts", "lang": "en"},
                )
                assert attempts.status_code == 200, attempts.text
                assert "Failed" in attempts.text and failed["stage"] in attempts.text
                assert '<table class="processing-attempts-table">' in attempts.text
                for heading in ("Time", "Processing type", "Original text", "Processing result"):
                    assert heading in attempts.text
                for number, heading in (("1", "Input"), ("2", "Processing"), ("3", "Output")):
                    assert f'<span class="processing-step-number">{number}</span>{heading}' in attempts.text
                assert f'data-bs-target="#attempt-{failed["attempt_id"]}"' in attempts.text
                assert TEXT in plain(attempts.text)
                summary_section = attempts.text.split('id="processing-summary"', 1)[1].split("</section>", 1)[0]
                assert 'data-summary="changes"' in summary_section
                assert "failed runs" in summary_section
                assert "<details" not in summary_section
                now = datetime.now(UTC)
                outside = await client.get(
                    f"/v1/scopes/{scope.scope_id}/processing/summary",
                    params={
                        "since": (now - timedelta(days=2)).isoformat(),
                        "until": (now - timedelta(days=1)).isoformat(),
                    },
                )
                assert outside.json()["attempts"] == 0
                assert outside.json()["inputs_waiting"] == 1

                assert "private-provider" not in str(failed)
                extractor.broken = False
                if fault == "commit":
                    async with runtime.primary_database.transaction() as connection:
                        await connection.exec_driver_sql("DROP TRIGGER reject_processing_success")
                await memory.flush(limit=1)
                records = (await client.get(path)).json()["items"]
                assert [record["status"] for record in records] == ["succeeded", "failed"]
                assert records[0]["previous_attempt_id"] == failed["attempt_id"]
                assert len(records[0]["results"]) == 1
                detail = await client.get(
                    "/dashboard/processing", params={"scope": scope.scope_id, "source_id": "input", "lang": "en"}
                )
                assert detail.status_code == 200, detail.text
                assert len(re.findall(r"<h1\b", detail.text)) == 1
                assert "Attempt 2 (latest)" in detail.text
                assert "<h2>Processing reason</h2>" in detail.text
                steps = detail.text.split('<li class="processing-step">')
                assert "Content saved in this attempt (1)" not in steps[2]
                assert "Content saved in this attempt (1)" in steps[3]
                catalog = await client.get(
                    "/dashboard/processing", params={"scope": scope.scope_id, "record_type": "sources", "lang": "en"}
                )
                rows = re.findall(r'<tr class="processing-source-row">(.*?)</tr>', catalog.text, re.S)
                assert len(rows) == 1
                assert re.search(r'<td data-processing-count><span class="badge[^"]*">2</span></td>', rows[0])
                assert re.search(r'<h3\b[^>]*class="processing-attempt-title"', detail.text)
                assert '<h2 class="processing-history-title">' in detail.text
                html_match = re.search(
                    r'<article class="processing-past-attempt processing-history-entry">(.*?)</article>',
                    detail.text,
                    re.S,
                )
                assert html_match is not None
                historical = html_match[1]
                for number, step in (("1", "Input"), ("2", "Processing"), ("3", "Output")):
                    assert f'<span class="processing-step-number">{number}</span>{step}</h4>' in historical
                assert historical.count('class="processing-history-row"') == 3
                assert 'class="processing-history-content"' in historical
                assert "Attempt 1" in historical
                assert "No new outputs were recorded." in historical
                summary = (await client.get(f"/v1/scopes/{scope.scope_id}/processing/summary")).json()
                assert summary["attempts"] == 2
                assert summary["failed_attempts"] == 1
                assert summary["changed_attempts"] == 1
                assert summary["created"] == 1
                assert summary["inputs_completed"] == 1
                assert summary["inputs_failed"] == 0
                assert summary["inputs_waiting"] == 0
                overview = await client.get("/dashboard/processing", params={"scope": scope.scope_id, "lang": "en"})
                assert "1 failed run" in overview.text
                assert ("Saving results" if fault == "commit" else "Extraction and comparison") in overview.text
                assert "1 input has no successful run" not in overview.text
                assert "including runs followed by a successful retry" in overview.text
                home = await client.get("/dashboard/home", params={"scope": scope.scope_id, "lang": "en"})
                assert home.status_code == 200, home.text
                recent = home.text.split('id="recent-processing"', 1)[1].split("</section>", 1)[0]
                assert TEXT in recent and "Processing failed" not in recent
                first = await client.get(path, params={"limit": 1})
                following = first.json()["next_cursor"]
                second = await client.get(path, params={"limit": 1, "cursor": following})
                assert second.json()["items"] == [failed]
                forged = await client.get(path.replace("/input/", "/other/"), params={"cursor": following})
                assert forged.status_code in {400, 404}

    asyncio.run(scenario())


def test_empty_extraction_is_recorded_without_inventing_a_reason(tmp_path):
    async def scenario():
        async with open_builtin_runtime(
            BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/empty.db")),
            candidate_pipeline=pipeline(),
        ) as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            scope = await runtime.scopes.create(ScopeDraft(title="Empty", summary="Results", idempotency_key="e"))
            await runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(source_id="empty", content="No candidate.", metadata={})
            )
            await runtime.atomic_memory.for_scope(scope.scope_id).flush(limit=1)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app_for(runtime)), base_url="http://test"
            ) as client:
                record = (await client.get(f"/v1/scopes/{scope.scope_id}/sources/content/empty/processing")).json()[
                    "items"
                ][0]
                assert record["status"] == "succeeded" and record["outcome"] == "no_candidates"
                assert record["decisions"] == record["results"] == []

    asyncio.run(scenario())


def test_processing_reader_requires_access_to_compared_and_published_artifacts(tmp_path, monkeypatch):
    from powercontext.builtin.runtime.atomic_memory import AtomicMemoryAccess
    from powercontext.server.authz import AccessDeniedError

    async def scenario():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/private.db"))
        async with open_builtin_runtime(config, candidate_pipeline=pipeline()) as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Private", summary="Results", idempotency_key="private")
            )
            await runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(source_id="input", content=TEXT, metadata={})
            )
            await runtime.atomic_memory.for_scope(scope.scope_id).flush(limit=1)
            authorize = AtomicMemoryAccess.authorize

            async def deny_artifact(self, connection, scope_id, context, action, ref=None):
                if action == "read" and ref is not None:
                    raise AccessDeniedError
                return await authorize(self, connection, scope_id, context, action, ref)

            monkeypatch.setattr(AtomicMemoryAccess, "authorize", deny_artifact)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app_for(runtime)), base_url="http://test"
            ) as client:
                response = await client.get(f"/v1/scopes/{scope.scope_id}/sources/content/input/processing")
                assert response.status_code == 403, response.text
                assert "Keep the new" not in response.text and '"attempt_id"' not in response.text

    asyncio.run(scenario())


def test_unconfigured_generation_records_an_actionable_failure(tmp_path):
    async def scenario():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/unconfigured.db"))
        async with open_builtin_runtime(config) as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Unconfigured", summary="Results", idempotency_key="u")
            )
            await runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(source_id="input", content=TEXT, metadata={})
            )
            with pytest.raises(InferenceUnavailableError):
                await runtime.atomic_memory.for_scope(scope.scope_id).flush(limit=1)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app_for(runtime)), base_url="http://test"
            ) as client:
                record = (await client.get(f"/v1/scopes/{scope.scope_id}/sources/content/input/processing")).json()[
                    "items"
                ][0]
                assert record["status"] == "failed" and record["error_code"] == "generation_unavailable"
                page = await client.get(
                    "/dashboard/processing", params={"scope": scope.scope_id, "source_id": "input", "lang": "en"}
                )
                assert "Memory generation is not configured or is currently unavailable." in page.text

    asyncio.run(scenario())


def test_killed_processor_remains_unconfirmed_and_can_be_retried(tmp_path):
    database = tmp_path / "killed.db"
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"))

    async def seed():
        async with open_builtin_runtime(config) as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            scope = await runtime.scopes.create(ScopeDraft(title="Interrupted", summary="Results", idempotency_key="k"))
            await runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(source_id="input", content=TEXT, metadata={})
            )
            return scope.scope_id

    scope_id = asyncio.run(seed())
    script = """
import asyncio, os, sys
from powercontext.builtin.runtime import BuiltinConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.composition import open_builtin_runtime
from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryGenerationPipeline
from powercontext.builtin.inference import character_token_estimator
from tests.e2e.test_memory_processing_records import DuplicateReconciler
class Extractor:
    async def generate(self, request, /):
        os._exit(17)
async def run():
    pipeline = AtomicMemoryGenerationPipeline(extractor=Extractor(), reconciler=DuplicateReconciler(), estimator=character_token_estimator())
    async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig(url=sys.argv[1])), candidate_pipeline=pipeline) as runtime:
        await runtime.atomic_memory.for_scope(sys.argv[2]).flush(limit=1)
asyncio.run(run())
"""
    child = subprocess.run(
        [sys.executable, "-c", script, f"sqlite+aiosqlite:///{database}", scope_id],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert child.returncode == 17, child.stderr

    async def verify():
        async with (
            open_builtin_runtime(config, candidate_pipeline=pipeline()) as runtime,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app_for(runtime)),
                base_url="http://test",
            ) as client,
        ):
            path = f"/v1/scopes/{scope_id}/sources/content/input/processing"
            record = (await client.get(path)).json()["items"][0]
            assert record["status"] == "started" and record["completed_at"] is None
            page = await client.get(
                "/dashboard/processing", params={"scope": scope_id, "source_id": "input", "lang": "en"}
            )
            assert "Started; completion unconfirmed" in page.text
            assert runtime.atomic_memory is not None
            await runtime.atomic_memory.for_scope(scope_id).flush(limit=1)
            completed = (await client.get(path)).json()["items"][0]
            assert completed["status"] == "succeeded"
            assert completed["previous_attempt_id"] == record["attempt_id"]

    asyncio.run(verify())


def test_scope_history_pages_every_attempt_and_rejects_loose_filters(tmp_path):
    from datetime import UTC, datetime, timedelta

    config = BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/scope.db"),
        runtime=RuntimeConfig(atomic_memory_related_mode="fts"),
    )

    async def scenario():
        async with open_builtin_runtime(config, candidate_pipeline=pipeline()) as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            scope = await runtime.scopes.create(ScopeDraft(title="Scope", summary="History", idempotency_key="s"))
            sources = runtime.sources.for_scope(scope.scope_id)
            memory = runtime.atomic_memory.for_scope(scope.scope_id)
            await sources.capture(CaptureSource(source_id="first", content=TEXT, metadata={}))
            await memory.flush(limit=1)
            await sources.capture(CaptureSource(source_id="duplicate", content=TEXT, metadata={}))
            await memory.flush(limit=1)
            base = f"/v1/scopes/{scope.scope_id}/processing"
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app_for(runtime)), base_url="http://test"
            ) as client:
                page = (await client.get(base)).json()
                assert [item["outcome"] for item in page["items"]] == ["no_change", "changed"]
                assert [item["scope_id"] for item in page["items"]] == [scope.scope_id, scope.scope_id]
                succeeded = (await client.get(base, params={"status": "succeeded"})).json()
                assert len(succeeded["items"]) == 2
                failed = (await client.get(base, params={"status": "failed"})).json()
                assert failed["items"] == []
                now = datetime.now(UTC)
                future = (await client.get(base, params={"since": (now + timedelta(days=1)).isoformat()})).json()
                assert future["items"] == []
                past = (await client.get(base, params={"until": (now - timedelta(days=1)).isoformat()})).json()
                assert past["items"] == []
                windowed = (
                    await client.get(
                        base,
                        params={
                            "since": (now - timedelta(days=1)).isoformat(),
                            "until": (now + timedelta(days=1)).isoformat(),
                        },
                    )
                ).json()
                assert len(windowed["items"]) == 2
                first = (await client.get(base, params={"limit": 1})).json()
                second = (await client.get(base, params={"limit": 1, "cursor": first["next_cursor"]})).json()
                assert first["items"][0]["outcome"] == "no_change"
                assert second["items"][0]["outcome"] == "changed"
                assert second["next_cursor"] is None
                assert (await client.get(base, params={"source_id": "first"})).status_code in {400, 422}
                forged = await client.get(base, params={"limit": 1, "cursor": first["next_cursor"], "status": "failed"})
                assert forged.status_code in {400, 404}
                summary = (await client.get(f"{base}/summary")).json()
                assert summary["attempts"] == 2
                assert summary["changed_attempts"] == 1
                assert summary["created"] == 1
                assert summary["updated"] == 0
                assert summary["empty_attempts"] == 1
                assert summary["failed_attempts"] == 0
                windowed = (
                    await client.get(
                        f"{base}/summary",
                        params={
                            "since": (now - timedelta(days=1)).isoformat(),
                            "until": (now + timedelta(days=1)).isoformat(),
                        },
                    )
                ).json()
                assert windowed["attempts"] == 2
                assert (
                    await client.get(f"{base}/summary", params={"until": (now - timedelta(days=1)).isoformat()})
                ).json()["attempts"] == 0
                assert (
                    await client.get(
                        f"{base}/summary",
                        params={
                            "since": (now - timedelta(days=1)).isoformat(),
                            "until": (now + timedelta(days=100)).isoformat(),
                        },
                    )
                ).status_code in {400, 422}
                attempts = await client.get(
                    "/dashboard/processing",
                    params={"scope": scope.scope_id, "record_type": "attempts", "lang": "en"},
                )
                assert attempts.status_code == 200, attempts.text
                assert "No change" in attempts.text and "Produced changes" in attempts.text
                assert TEXT in attempts.text
                assert "Completed" in attempts.text
                assert "2 attempts" in attempts.text and "1 created" in attempts.text
                assert "1 without changes" in attempts.text
                overview = await client.get("/dashboard/processing", params={"scope": scope.scope_id, "lang": "en"})
                assert overview.status_code == 200, overview.text
                assert "Processing results for this period" in overview.text
                assert "Sources with processing records 2 inputs" in plain(overview.text)
                assert "Processed 2 inputs" in plain(overview.text)
                assert "1 created · 0 updated" in plain(overview.text)
                assert "Nothing needs attention in this period." not in overview.text
                assert "This status covers Topic Memory only." in overview.text
                today = datetime.now(UTC).strftime("%Y-%m-%d")
                day_page = await client.get(
                    "/dashboard/processing",
                    params={
                        "scope": scope.scope_id,
                        "record_type": "attempts",
                        "processing_day": today,
                        "lang": "en",
                    },
                )
                assert day_page.status_code == 200, day_page.text
                assert "No change" in day_page.text
                empty_day = await client.get(
                    "/dashboard/processing",
                    params={
                        "scope": scope.scope_id,
                        "record_type": "attempts",
                        "processing_day": "2000-01-01",
                        "lang": "en",
                    },
                )
                assert "No automatic processing was recorded for this day." in empty_day.text

    asyncio.run(scenario())


def test_overview_alerts_on_unresolved_failure(tmp_path):
    class BrokenExtractor(Extractor):
        async def generate(self, request, /):
            raise RuntimeError("demo provider outage")  # noqa: TRY003

    async def scenario():
        selected = AtomicMemoryGenerationPipeline(
            extractor=BrokenExtractor(),
            reconciler=DuplicateReconciler(),
            estimator=character_token_estimator(),
        )
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/overview.db"))
        async with open_builtin_runtime(config, candidate_pipeline=selected) as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            scope = await runtime.scopes.create(ScopeDraft(title="Alerting", summary="Failures", idempotency_key="a"))
            await runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(source_id="input", content=TEXT, metadata={})
            )
            with pytest.raises(RuntimeError):
                await runtime.atomic_memory.for_scope(scope.scope_id).flush(limit=1)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app_for(runtime)), base_url="http://test"
            ) as client:
                overview = await client.get("/dashboard/processing", params={"scope": scope.scope_id, "lang": "en"})
                assert overview.status_code == 200, overview.text
                assert "1 input has no successful run" in overview.text
                assert "Sources with processing records 1 inputs" in plain(overview.text)
                assert "Processed 0 inputs" in plain(overview.text)
                assert "Extraction and comparison" in overview.text
                assert "still need processing" not in overview.text
                assert "1 failed run" in overview.text

    asyncio.run(scenario())


def test_unattempted_input_surfaces_instead_of_looking_healthy(tmp_path):
    async def scenario():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/waiting.db"))
        async with open_builtin_runtime(config, candidate_pipeline=pipeline()) as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            scope = await runtime.scopes.create(ScopeDraft(title="Stalled", summary="Queue", idempotency_key="w"))
            await runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(source_id="stuck", content=TEXT, metadata={})
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app_for(runtime)), base_url="http://test"
            ) as client:
                base = f"/v1/scopes/{scope.scope_id}/processing/summary"
                waiting = (await client.get(base)).json()
                assert waiting["attempts"] == 0
                assert waiting["inputs_total"] == 0
                assert waiting["inputs_waiting"] == 1
                assert waiting["inputs_waiting_is_lower_bound"] is False
                page = await client.get("/dashboard/processing", params={"scope": scope.scope_id, "lang": "en"})
                assert "1 input still needs processing" in page.text
                assert "Nothing needs attention in this period." not in page.text
                await runtime.atomic_memory.for_scope(scope.scope_id).flush(limit=1)
                after = (await client.get(base)).json()
                assert after["inputs_waiting"] == 0
                settled = await client.get("/dashboard/processing", params={"scope": scope.scope_id, "lang": "en"})
                assert "still need processing" not in settled.text

    asyncio.run(scenario())


def test_old_unresolved_failure_remains_visible_after_leaving_the_period(tmp_path):
    async def scenario():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/old-failure.db"))
        async with open_builtin_runtime(config) as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            scope = await runtime.scopes.create(ScopeDraft(title="Old failure", summary="Queue", idempotency_key="old"))
            with time_machine.travel(datetime.now(UTC) - timedelta(days=10), tick=False):
                await runtime.sources.for_scope(scope.scope_id).capture(
                    CaptureSource(source_id="stuck", content=TEXT, metadata={})
                )
                with pytest.raises(InferenceUnavailableError):
                    await runtime.atomic_memory.for_scope(scope.scope_id).flush(limit=1)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app_for(runtime)), base_url="http://test"
            ) as client:
                summary = await client.get(f"/v1/scopes/{scope.scope_id}/processing/summary")
                assert summary.status_code == 200, summary.text
                assert summary.json()["attempts"] == 0
                assert summary.json()["inputs_failed"] == 0
                assert summary.json()["inputs_waiting"] == 1
                page = await client.get("/dashboard/processing", params={"scope": scope.scope_id, "lang": "en"})
                assert page.status_code == 200, page.text
                assert "1 input still needs processing" in page.text
                assert "Nothing needs attention in this period." not in page.text
        async with open_builtin_runtime(config, candidate_pipeline=pipeline()) as runtime:
            assert runtime.atomic_memory is not None
            await runtime.atomic_memory.for_scope(scope.scope_id).flush(limit=1)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app_for(runtime)), base_url="http://test"
            ) as client:
                summary = await client.get(f"/v1/scopes/{scope.scope_id}/processing/summary")
                assert summary.status_code == 200, summary.text
                assert summary.json()["inputs_waiting"] == 0
                assert summary.json()["inputs_completed"] == 1

    asyncio.run(scenario())


def test_waiting_scan_reports_a_lower_bound(monkeypatch, tmp_path):
    from powercontext.builtin.runtime import scope_reads as scope_reads_module

    monkeypatch.setattr(scope_reads_module, "PROCESSING_WAITING_SCAN", 1)

    async def scenario():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/bound.db"))
        async with open_builtin_runtime(config, candidate_pipeline=pipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(ScopeDraft(title="Bound", summary="Cap", idempotency_key="b"))
            sources = runtime.sources.for_scope(scope.scope_id)
            for index in range(2):
                await sources.capture(CaptureSource(source_id=f"waiting-{index}", content=TEXT, metadata={}))
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app_for(runtime)), base_url="http://test"
            ) as client:
                summary = (await client.get(f"/v1/scopes/{scope.scope_id}/processing/summary")).json()
                assert summary["inputs_waiting"] == 1
                assert summary["inputs_waiting_is_lower_bound"] is True
                page = await client.get("/dashboard/processing", params={"scope": scope.scope_id, "lang": "en"})
                assert "At least 1 input still needs processing" in page.text

    asyncio.run(scenario())


def test_incomplete_waiting_scan_does_not_report_a_healthy_queue(monkeypatch, tmp_path):
    from powercontext.builtin.runtime import scope_reads as scope_reads_module

    monkeypatch.setattr(scope_reads_module, "PROCESSING_WAITING_SCAN", 1)

    async def scenario():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/incomplete-scan.db"))
        async with open_builtin_runtime(config) as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            scope = await runtime.scopes.create(ScopeDraft(title="Scan", summary="Queue", idempotency_key="scan"))
            await runtime.atomic_memory.for_scope(scope.scope_id).create((
                AtomicMemoryContent(kind="fact", text=TEXT, description="Direct save"),
            ))
            await runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(source_id="waiting", content=TEXT, metadata={})
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app_for(runtime)), base_url="http://test"
            ) as client:
                summary = await client.get(f"/v1/scopes/{scope.scope_id}/processing/summary")
                assert summary.status_code == 200, summary.text
                assert summary.json()["inputs_waiting"] == 0
                assert summary.json()["inputs_waiting_is_lower_bound"] is True
                page = await client.get("/dashboard/processing", params={"scope": scope.scope_id, "lang": "en"})
                assert page.status_code == 200, page.text
                assert "Some inputs have not been checked for completion" in page.text
                assert "Nothing needs attention in this period." not in page.text

    asyncio.run(scenario())


@pytest.mark.parametrize("window", ["today", "custom"])
def test_observation_period_filters_attempts_and_preserves_pagination(tmp_path, window):
    async def scenario():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/period.db"))
        async with open_builtin_runtime(config, candidate_pipeline=pipeline()) as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Period records", summary="Period boundaries", idempotency_key="period")
            )
            memory = runtime.atomic_memory.for_scope(scope.scope_id)
            sources = runtime.sources.for_scope(scope.scope_id)
            with time_machine.travel("2026-10-20T09:00:00Z", tick=False):
                await sources.capture(CaptureSource(source_id="older", content="No candidate.", metadata={}))
                await memory.flush(limit=1)
            with time_machine.travel("2026-11-07T09:00:00Z", tick=False):
                for index in range(8):
                    await sources.capture(
                        CaptureSource(source_id=f"current-{index}", content="No candidate.", metadata={})
                    )
                    await memory.flush(limit=1)
                app = app_for(runtime)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    params = {"scope": scope.scope_id, "record_type": "attempts", "period": "today", "lang": "en"}
                    if window == "custom":
                        params.update(
                            period="7d",
                            processing_period="custom",
                            processing_start="2026-11-07",
                            processing_end="2026-11-07",
                        )
                    first = await client.get("/dashboard/processing", params=params)
                    assert first.status_code == 200, first.text
                    assert "8 attempts" in first.text
                    assert "2026-10-20" not in first.text
                    from tests.test_dashboard import page_link

                    with time_machine.travel("2026-11-07T09:01:00Z", tick=False):
                        following = await client.get(page_link(first.text, "Next page"))
                    assert following.status_code == 200, following.text
                    assert "2026-10-20" not in following.text
                    month = await client.get(
                        "/dashboard/processing",
                        params={"scope": scope.scope_id, "record_type": "attempts", "period": "30d", "lang": "en"},
                    )
                    assert month.status_code == 200, month.text
                    assert "9 attempts" in month.text

    asyncio.run(scenario())


def test_home_processing_includes_history_older_than_every_period(tmp_path):
    config = BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/all-history.db"),
        runtime=RuntimeConfig(atomic_memory_related_mode="fts"),
    )

    async def scenario():
        async with open_builtin_runtime(config, candidate_pipeline=pipeline()) as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="All history", summary="Processing history", idempotency_key="all-history")
            )
            old = datetime.now(UTC) - timedelta(days=120)
            with time_machine.travel(old, tick=False):
                await runtime.sources.for_scope(scope.scope_id).capture(
                    CaptureSource(source_id="old-source", content=TEXT, metadata={})
                )
                await runtime.atomic_memory.for_scope(scope.scope_id).flush(limit=1)
            app = app_for(runtime)
            app.state.dashboard_runtime = runtime
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                # The public bounded report retains its existing seven-day default.
                summary = await client.get(f"/v1/scopes/{scope.scope_id}/processing/summary")
                assert summary.json()["attempts"] == 0
                for period in ("today", "7d", "30d"):
                    home = await client.get(
                        "/dashboard/home", params={"scope": scope.scope_id, "period": period, "lang": "en"}
                    )
                    assert home.status_code == 200, home.text
                    section = home.text.split('<section id="recent-processing"', 1)[1].split("</section>", 1)[0]
                    assert TEXT in plain(section)
                    assert 'class="home-metric-cell"' not in section

    asyncio.run(scenario())
