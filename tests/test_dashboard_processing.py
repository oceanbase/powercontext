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

"""Check exact memory evidence and processing navigation against persisted records."""

import asyncio
import re
from datetime import UTC, datetime
from html import unescape
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, CreateDreamRunRequest, GetDreamRunRequest
from powercontext.server.app import create_app
from powercontext.server.dashboard.routes import LABELS, router
from tests.e2e.dream_support import atomic_memory_pipeline, open_dream_runtime, process_pending
from tests.e2e.test_artifact_dreaming import Generator, MemoryPipeline, NoChangeGenerator, seed
from tests.test_dashboard import create_scope
from tests.test_dashboard import dashboard as dashboard
from tests.test_dashboard_profile_handoff import _revised_memory


def links(html, path):
    return [unescape(url) for url in re.findall(r'href="([^"]+)"', html) if urlsplit(unescape(url)).path == path]


@pytest.mark.parametrize("public_reads", [False, True])
def test_memory_source_reader_is_bound_to_exact_revision(dashboard, monkeypatch, public_reads):
    scope = create_scope(dashboard, "Exact memory evidence")["scope_id"]
    artifact = _revised_memory(dashboard, scope)
    path = f"/v1/scopes/{scope}/artifacts/atomic-memory/{artifact}/revisions"
    old = dashboard.get(f"{path}/1").json()
    new = dashboard.get(f"{path}/2").json()
    original_source = old["sources"][0]["source_id"]
    later_source = next(item["source_id"] for item in new["sources"] if item["source_id"] != original_source)
    if public_reads:
        monkeypatch.setattr("powercontext.server.dashboard.api._builtin_runtime", lambda request: None)
    page = dashboard.get("/dashboard/notes", params={"scope": scope, "artifact": artifact, "revision": 1})
    assert page.status_code == 200, page.text
    evidence_url = links(page.text, f"/dashboard/evidence/{original_source}")[0]
    evidence = dashboard.get(evidence_url, headers={"HX-Request": "true"})
    assert evidence.status_code == 200, evidence.text
    assert "Original note." in evidence.text
    assert "Revised note." not in evidence.text
    forged = dashboard.get(evidence_url.replace(original_source, later_source))
    assert forged.status_code == 404, forged.text
    assert "Revised note." not in forged.text
    assert later_source not in page.text
    other = create_scope(dashboard, "Foreign evidence")["scope_id"]
    crossed = dashboard.get(evidence_url.replace(scope, other))
    assert crossed.status_code in {403, 404}
    assert "Original note." not in crossed.text


@pytest.mark.parametrize("public_reads", [False, True])
def test_memory_source_buttons_open_each_recorded_source(tmp_path, monkeypatch, public_reads):
    from powercontext.builtin.artifacts.atomic_memory.extraction import (
        AtomicMemoryCandidate,
        AtomicMemoryExtractionOutput,
    )
    from powercontext.builtin.inference import GenerationResult
    from powercontext.builtin.runtime import CaptureSource
    from powercontext.builtin.scope import ScopeDraft

    class CombinedPipeline:
        async def generate(self, request, /):
            return GenerationResult(
                output=AtomicMemoryExtractionOutput(
                    candidates=(
                        AtomicMemoryCandidate(
                            kind="fact",
                            text="Both sources support this fact.",
                            description="Combined source evidence",
                            evidence_ids=tuple(item.evidence_id for item in request.evidence),
                        ),
                    )
                )
            )

    async def scenario():
        async with open_dream_runtime(
            BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/buttons.db")),
            candidate_pipeline=atomic_memory_pipeline(CombinedPipeline()),
        ) as runtime:
            scope = (
                await runtime.scopes.create(
                    ScopeDraft(title="Multiple source buttons", summary="Exact evidence", idempotency_key="buttons")
                )
            ).scope_id
            for index in range(2):
                await runtime.sources.for_scope(scope).capture(
                    CaptureSource(source_id=f"source-{index}", content=f"Evidence body {index}.", metadata={})
                )
            await runtime.atomic_memory.for_scope(scope).flush()
            memory = (await runtime.atomic_memory.for_scope(scope).list()).items[0]
            app = create_app(application=runtime)
            app.include_router(router, prefix="/dashboard")
            if not public_reads:
                app.state.dashboard_runtime = runtime
            else:
                monkeypatch.setattr("powercontext.server.dashboard.api._builtin_runtime", lambda request: None)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                page = await client.get(
                    "/dashboard/notes",
                    params={
                        "scope": scope,
                        "artifact": memory.ref.artifact_id,
                        "revision": memory.ref.revision,
                        "lang": "en",
                    },
                )
                assert page.status_code == 200, page.text
                direct = page.text.split('aria-label="Sources for this revision"', 1)[1]
                direct = direct.split('<details class="supporting-details"', 1)[0]
                buttons = re.findall(r'<a class="evidence-link" href="([^"]+)"', direct)
                assert len(buttons) == 2
                for index, button in enumerate(buttons):
                    destination = unescape(button)
                    assert urlsplit(destination).path == f"/dashboard/evidence/source-{index}"
                    evidence = await client.get(destination, headers={"HX-Request": "true"})
                    assert evidence.status_code == 200, evidence.text
                    assert f"Evidence body {index}." in evidence.text

    asyncio.run(scenario())


def test_memory_source_login_returns_to_the_exact_memory(dashboard):
    scope = create_scope(dashboard, "Memory evidence login")["scope_id"]
    artifact = _revised_memory(dashboard, scope)
    old = dashboard.get(f"/v1/scopes/{scope}/artifacts/atomic-memory/{artifact}/revisions/1").json()
    page = dashboard.get("/dashboard/notes", params={"scope": scope, "artifact": artifact, "revision": 1, "lang": "en"})
    source_url = links(page.text, f"/dashboard/evidence/{old['sources'][0]['source_id']}")[0]
    token = dashboard.headers.pop("Authorization").removeprefix("Bearer ")
    login = dashboard.get(source_url)
    assert login.status_code == 401
    match = re.search(r'name="next" value="([^"]+)"', login.text)
    assert match is not None
    destination = unescape(match[1])
    assert urlsplit(destination).path == "/dashboard/notes"
    assert (
        parse_qs(urlsplit(destination).query).items()
        >= {"scope": [scope], "artifact": [artifact], "revision": ["1"], "lang": ["en"]}.items()
    )
    resumed = dashboard.post(
        "/dashboard/session",
        data={"token": token, "next": destination},
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert resumed.status_code == 303
    assert resumed.headers["location"] == destination
    assert "Original note." in dashboard.get(destination).text


@pytest.mark.parametrize("public_reads", [False, True])
def test_processing_read_failure_does_not_appear_empty(dashboard, monkeypatch, public_reads):
    scope = create_scope(dashboard, "Processing failure")["scope_id"]
    from powercontext.server.dashboard.api import DashboardAPI
    from powercontext.server.dashboard.errors import ReadError

    original = DashboardAPI.read
    projection = DashboardAPI.project
    if public_reads:
        monkeypatch.setattr("powercontext.server.dashboard.api._builtin_runtime", lambda request: None)

    async def unavailable_projection(self, selected_scope, operation, **params):
        if operation == "source_directory":
            raise ReadError(503, "service_unavailable")
        return await projection(self, selected_scope, operation, **params)

    async def unavailable(self, path, payload=None):
        if path.startswith(f"/v1/scopes/{scope}/sources?"):
            raise ReadError(503, "service_unavailable")
        return await original(self, path, payload)

    monkeypatch.setattr(DashboardAPI, "read", unavailable)
    monkeypatch.setattr(DashboardAPI, "project", unavailable_projection)
    page = dashboard.get("/dashboard/processing", params={"scope": scope})
    assert page.status_code == 200
    assert LABELS["processing_sources_empty"] not in page.text
    assert LABELS["retry"] in page.text


def test_processing_audit_failure_does_not_expose_source(dashboard, monkeypatch):
    scope = create_scope(dashboard, "Protected processing title")["scope_id"]
    saved = dashboard.post(f"/v1/scopes/{scope}/sources", json={"content": "Protected source text."}).json()

    async def unavailable(events):
        raise RuntimeError

    monkeypatch.setattr(dashboard.app.state.access_control.audit, "append_audits", unavailable)
    page = dashboard.get("/dashboard/processing", params={"scope": scope, "source_id": saved["source_id"]})
    assert page.status_code == 503
    assert "Protected source text." not in page.text
    assert "Protected processing title" not in page.text


@pytest.mark.parametrize("outcome", ["proposed", "no_change", "failed"])
def test_processing_dream_displays_persisted_outcome_and_inputs(tmp_path, outcome):
    async def scenario():
        generator = NoChangeGenerator() if outcome == "no_change" else Generator(unknown_id=outcome == "failed")
        async with open_dream_runtime(
            BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/dream.db")),
            candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()),
            dream_generator=generator,
        ) as runtime:
            scope, source, citation = await seed(runtime)
            accepted = await runtime.dream.for_scope(scope).create(
                CreateDreamRunRequest(
                    operation="refine_experience", artifacts=(citation,), sources=(source,), idempotency_key="dashboard"
                )
            )
            app = create_app(application=runtime)
            app.include_router(router, prefix="/dashboard")
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                queued = await client.get(
                    "/dashboard/processing", params={"scope": scope, "record_type": "dreams", "run_id": accepted.run_id}
                )
                assert queued.status_code == 200, queued.text
                assert LABELS["processing_status_queued"] in queued.text
                assert queued.text.index('data-dream-stage="input"') < queued.text.index('data-dream-stage="process"')
                assert queued.text.index('data-dream-stage="process"') < queued.text.index('data-dream-stage="output"')
                assert 'hx-trigger="every 3s"' in queued.text
                await process_pending(runtime)
                run = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=accepted.run_id))
                assert run.status == ("failed" if outcome == "failed" else "succeeded"), run.error
                page = await client.get(
                    "/dashboard/processing", params={"scope": scope, "record_type": "dreams", "run_id": run.run_id}
                )
                assert page.status_code == 200, page.text
                assert source.source_id in page.text
                inputs = re.search(r'<li\b[^>]*data-dream-stage="input"[^>]*>(.*?)</li>', page.text, re.S)
                assert inputs is not None
                assert "The replay test passed without duplicate writes." in inputs[1]
                assert "UNSELECTED_SIBLING_SENTINEL" not in page.text
                references = re.search(
                    r"<details\b([^>]*)data-dream-references([^>]*)>(.*?)</details>", page.text, re.S
                )
                assert references is not None
                assert "open" not in (references[1] + references[2]).split()
                assert source.source_id in references[3]
                assert citation.artifact_id in references[3]
                assert page.text.index('data-dream-stage="input"') < page.text.index('data-dream-stage="process"')
                assert page.text.index('data-dream-stage="process"') < page.text.index('data-dream-stage="output"')
                output = re.search(r'<li\b[^>]*data-dream-stage="output"[^>]*>(.*?)</li>', page.text, re.S)
                assert output is not None
                if run.candidate:
                    assert run.candidate.candidate_id in output[1]
                else:
                    assert LABELS["processing_dream_no_output"] in output[1]
                if outcome == "failed":
                    assert run.error and run.error in page.text
                    assert LABELS["processing_status_failed"] in page.text
                else:
                    assert run.outcome == outcome
                    assert LABELS["processing_outcome_" + outcome] in page.text
                if run.candidate:
                    assert any(run.candidate.candidate_id in url for url in links(page.text, "/dashboard/review"))
                    review_url = next(
                        url for url in links(page.text, "/dashboard/review") if run.candidate.candidate_id in url
                    )
                    opened_review = await client.get(review_url)
                    assert opened_review.status_code == 200, opened_review.text
                filtered = await client.get(
                    "/dashboard/processing", params={"scope": scope, "record_type": "dreams", "run_status": "failed"}
                )
                assert filtered.status_code == 200, filtered.text
                assert (run.run_id in filtered.text) == (outcome == "failed")
                assert ("processing-runs-table" in filtered.text) == (outcome == "failed")
                from datetime import UTC, datetime

                for params in (
                    {"record_type": "attempts", "run_status": "succeeded"},
                    {"record_type": "attempts", "processing_day": datetime.now(UTC).date().isoformat()},
                    {"record_type": "dreams", "run_status": run.status},
                    {"record_type": "dreams", "run_status": run.status, "run_id": run.run_id},
                ):
                    selected = await client.get("/dashboard/processing", params={"scope": scope, **params})
                    assert selected.status_code == 200, selected.text
                    source_links = [
                        url
                        for url in links(selected.text, "/dashboard/processing")
                        if parse_qs(urlsplit(url).query).get("source_id") == [source.source_id]
                    ]
                    assert source_links, params
                    for destination in source_links:
                        opened = await client.get(destination)
                        assert opened.status_code == 200, (destination, opened.text)
                        assert "Replayed the write with key K" in opened.text
                if outcome == "failed":
                    assert LABELS["processing_table_inputs"] in filtered.text
                    assert LABELS["processing_operation_refine_experience"] in filtered.text

    asyncio.run(scenario())


@pytest.mark.parametrize("public_reads", [False, True])
def test_processing_sources_show_save_time_before_processing(dashboard, monkeypatch, public_reads):

    scope = create_scope(dashboard, "Source save time")["scope_id"]
    before = datetime.now(UTC).replace(second=0, microsecond=0)
    saved = dashboard.post(f"/v1/scopes/{scope}/sources", json={"content": "Source saved without processing."})
    assert saved.status_code == 201, saved.text
    if public_reads:
        monkeypatch.setattr("powercontext.server.dashboard.api._builtin_runtime", lambda request: None)
    page = dashboard.get("/dashboard/processing", params={"scope": scope, "record_type": "sources", "lang": "en"})
    assert page.status_code == 200
    assert '<th scope="col">Saved at</th>' in page.text
    row = re.search(r'<tr class="processing-source-row">(.*?)</tr>', page.text, re.S)
    assert row is not None
    timestamp = re.search(r"<time[^>]*>(.*?)</time>", row[1])
    if public_reads:
        assert timestamp is None
        assert "Not reported" in row[1]
        return
    assert timestamp is not None
    at = datetime.strptime(timestamp[1], "%Y-%m-%d %H:%M UTC").replace(tzinfo=UTC)
    assert before <= at <= datetime.now(UTC)
    assert "Latest processing time" not in page.text
