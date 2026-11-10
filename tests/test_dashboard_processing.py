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
from tests.test_dashboard import create_scope, page_link
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


def test_processing_sources_page_in_scope_and_links_exact_result(dashboard):
    scope = create_scope(dashboard, "Source processing")["scope_id"]
    empty = create_scope(dashboard, "Empty source scope", parent=scope)["scope_id"]
    artifact = _revised_memory(dashboard, scope)
    old = dashboard.get(f"/v1/scopes/{scope}/artifacts/atomic-memory/{artifact}/revisions/1").json()
    for index in range(7):
        result = dashboard.post(f"/v1/scopes/{scope}/sources", json={"content": f"Source record {index}."})
        assert result.status_code == 201, result.text
    first = dashboard.get("/dashboard/processing", params={"scope": scope, "record_type": "sources"})
    assert first.status_code == 200, first.text
    assert '<table class="processing-attempts-table processing-sources-table">' in first.text
    assert LABELS["processing_source_saved_time"] in first.text
    assert LABELS["processing_table_source_id"] in first.text
    assert f'<th scope="col">{LABELS["processing_table_original"]}</th>' in first.text
    assert LABELS["processing_source_type"] in first.text and LABELS["processing_table_status"] in first.text
    assert "核对 Agent 使用记录" not in first.text
    second = dashboard.get(page_link(first.text, LABELS["next_page"]))
    assert second.status_code == 200, second.text
    first_sources = {
        parse_qs(urlsplit(url).query)["source_id"][0]
        for url in links(first.text, "/dashboard/processing")
        if "source_id=" in url
    }
    second_sources = {
        parse_qs(urlsplit(url).query)["source_id"][0]
        for url in links(second.text, "/dashboard/processing")
        if "source_id=" in url
    }
    assert len(first_sources) == 6
    assert second_sources and first_sources.isdisjoint(second_sources)
    source = old["sources"][0]["source_id"]
    detail = dashboard.get("/dashboard/processing", params={"scope": scope, "source_id": source})
    assert detail.status_code == 200, detail.text
    assert "Original note." in detail.text
    assert LABELS["processing_step_input"] in detail.text
    assert LABELS["processing_step_process"] in detail.text
    assert LABELS["processing_step_output"] in detail.text
    result_link = next(url for url in links(detail.text, "/dashboard/notes") if "artifact=" in url)
    assert parse_qs(urlsplit(result_link).query)["revision"] == ["1"]
    assert "Original note." in dashboard.get(result_link).text
    other = dashboard.get("/dashboard/processing", params={"scope": empty, "source_id": source})
    assert other.status_code == 404
    assert "Original note." not in other.text
    empty_page = dashboard.get("/dashboard/processing", params={"scope": empty, "record_type": "sources"})
    assert empty_page.status_code == 200
    assert LABELS["processing_sources_empty"] in empty_page.text
    assert "Source record" not in empty_page.text


def test_processing_sources_show_latest_memory_result_preview(dashboard, monkeypatch):
    from powercontext.server.dashboard.api import DashboardAPI

    scope = create_scope(dashboard, "Source result preview")["scope_id"]
    saved = dashboard.post(f"/v1/scopes/{scope}/sources", json={"content": "Source with a processed result."})
    assert saved.status_code == 201, saved.text
    source_id = saved.json()["source_id"]
    artifact = _revised_memory(dashboard, scope)
    original = DashboardAPI.read

    async def history(self, path, payload=None):
        if f"/sources/content/{source_id}/processing?" in path:
            return {
                "items": [
                    {
                        "results": [
                            {
                                "action": "revise",
                                "artifact": {"family": "atomic-memory", "artifact_id": artifact, "revision": 2},
                                "source_refs": [{"source_type": "content", "source_id": source_id}],
                            }
                        ]
                    }
                ],
                "next_cursor": None,
            }
        return await original(self, path, payload)

    monkeypatch.setattr(DashboardAPI, "read", history)
    page = dashboard.get("/dashboard/processing", params={"scope": scope, "record_type": "sources", "lang": "en"})
    assert page.status_code == 200, page.text
    assert "processing-result-preview" in page.text
    assert "Revised note." in page.text and "Updated" in page.text
    assert "Source with a processed result." in page.text
    assert '<th scope="col">Source type</th>' in page.text and '<th scope="col">Processing status</th>' in page.text
    assert "<td>Content</td>" in page.text


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
                CreateDreamRunRequest(operation="refine_experience", artifacts=(citation,), idempotency_key="dashboard")
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
                if outcome == "failed":
                    assert LABELS["processing_table_inputs"] in filtered.text
                    assert LABELS["processing_operation_refine_experience"] in filtered.text

    asyncio.run(scenario())


def test_direct_writes_do_not_look_like_waiting_processing(dashboard):
    scope = create_scope(dashboard, "Direct writes")["scope_id"]
    saved = dashboard.post(
        "/v1/memory/remember",
        json={"scope_id": scope, "kind": "fact", "text": "Direct note that does not enter extraction."},
    )
    assert saved.status_code == 200, saved.text
    summary = dashboard.get(f"/v1/scopes/{scope}/processing/summary")
    assert summary.status_code == 200, summary.text
    assert summary.json()["inputs_waiting"] == 0


@pytest.mark.parametrize("kind", ["attempts", "sources", "dreams"])
def test_runtime_records_have_peer_navigation_and_processing_help(dashboard, kind):
    scope = create_scope(dashboard, "Processing navigation")["scope_id"]
    page = dashboard.get("/dashboard/processing", params={"scope": scope, "record_type": kind, "lang": "en"})
    assert page.status_code == 200, page.text
    html_match = re.search(r"<aside\b.*?</aside>", page.text, re.S)
    assert html_match is not None
    sidebar = html_match[0]
    active_tags = re.findall(r'<a\b[^>]*aria-current="page"[^>]*>', sidebar)
    assert len(active_tags) == 1
    html_match = re.search(r'href="([^"]+)"', active_tags[0])
    assert html_match is not None
    active_href = unescape(html_match[1])
    assert parse_qs(urlsplit(active_href).query)["record_type"] == ["dreams" if kind == "dreams" else "sources"]
    html_match = re.search(r'<ul class="navbar-nav w-100".*?</ul>', sidebar, re.S)
    assert html_match is not None
    navigation = html_match[0]
    assert "record_type=attempts" not in navigation
    assert sidebar.count("Automatic processing</span>") == 1
    assert 'name="record_type" value="' + kind + '"' in sidebar
    expected = {"sources": "Automatic processing", "attempts": "Automatic processing", "dreams": "Dream processing"}[
        kind
    ]
    html_match = re.search(r"<h1\b[^>]*>(.*?)</h1>", page.text, re.S)
    assert html_match is not None
    assert html_match[1] == expected
    assert 'class="nav nav-pills mb-4 processing-tabs"' not in page.text
    assert f"<title>{expected} · PowerContext</title>" in page.text
    assert f'data-title="{expected} · PowerContext"' in page.text
    if kind != "sources":
        assert f'aria-describedby="processing-help-{kind}"' in page.text
        assert 'role="tooltip"' in page.text


def test_source_outputs_distinguish_topics_with_the_same_revision(dashboard, monkeypatch):
    scope = create_scope(dashboard, "Distinct related topics")["scope_id"]
    source = dashboard.post(f"/v1/scopes/{scope}/sources", json={"content": "Project workflow evidence."}).json()
    refs = []
    for name in ("Release workflow", "Review workflow"):
        created = dashboard.post(
            f"/v1/scopes/{scope}/artifacts",
            json={
                "family": "topic-memory",
                "content": {"title": name, "summary": name + " first summary.", "detail": name + " detail."},
            },
        )
        assert created.status_code == 201, created.text
        artifact = created.json()["artifact_id"]
        path = f"/v1/scopes/{scope}/artifacts/topic-memory/{artifact}"
        previous = dashboard.get(path)
        revised = dashboard.put(
            path,
            headers={"If-Match": previous.headers["etag"]},
            json={"content": {"title": name, "summary": name + " saved summary.", "detail": name + " second detail."}},
        )
        assert revised.status_code == 200, revised.text
        refs.append({"family": "topic-memory", "artifact_id": artifact, "revision": 2})

    # Supply the related-reference projection; exact bodies and links use real HTTP reads.
    async def related(snapshot, source_id, **params):
        assert snapshot.scope_id == scope and source_id == source["source_id"]
        return refs

    monkeypatch.setattr("powercontext.builtin.runtime.scope_reads.ScopeReadSnapshot.source_artifacts", related)
    page = dashboard.get(
        "/dashboard/processing",
        params={"scope": scope, "record_type": "sources", "source_id": source["source_id"], "lang": "en"},
    )
    assert page.status_code == 200, page.text
    disclosure = re.search(r"<details\b([^>]*data-linked-outputs[^>]*)>", page.text)
    assert disclosure is not None and "open" not in disclosure[1]
    rows = re.findall(r'<table class="processing-output-table">.*?<tbody>(.*?)</tbody>', page.text, re.S)
    assert len(rows) == 1
    assert "Release workflow" in rows[0] and "Review workflow" in rows[0]
    assert "Release workflow saved summary." in rows[0] and "Review workflow saved summary." in rows[0]
    destinations = links(rows[0], "/dashboard/topics")
    assert len(destinations) == 2
    for destination, ref in zip(destinations, refs, strict=True):
        params = parse_qs(urlsplit(destination).query)
        assert params["topic_artifact"] == [ref["artifact_id"]]
        assert params["topic_revision"] == ["2"]
        assert dashboard.get(destination).status_code == 200


@pytest.mark.parametrize("unavailable", [False, True])
def test_source_processing_count_distinguishes_truncated_and_unavailable_history(dashboard, monkeypatch, unavailable):
    from powercontext.server.dashboard.api import DashboardAPI
    from powercontext.server.dashboard.errors import ReadError

    scope = create_scope(dashboard, "Source execution count")["scope_id"]
    saved = dashboard.post(f"/v1/scopes/{scope}/sources", json={"content": "One saved input."})
    assert saved.status_code == 201
    source_id = saved.json()["source_id"]
    original = DashboardAPI.read

    async def history(self, path, payload=None):
        if f"/sources/content/{source_id}/processing?" in path:
            if unavailable:
                raise ReadError(503, "service_unavailable")
            return {"items": [{}] * 20, "next_cursor": "more-history"}
        return await original(self, path, payload)

    monkeypatch.setattr(DashboardAPI, "read", history)
    page = dashboard.get(
        "/dashboard/processing", params={"scope": scope, "record_type": "sources", "lang": "en", "period": "today"}
    )
    assert page.status_code == 200
    cells = re.findall(r"<td data-processing-count>(.*?)</td>", page.text, re.S)
    assert len(cells) == 1
    assert ("20+" in cells[0]) if not unavailable else ("Not reported" in cells[0])
    assert len(re.findall(r'<tr class="processing-source-row">', page.text)) == 1


@pytest.mark.parametrize("public_reads", [False, True])
def test_processing_sources_show_save_time_before_processing(dashboard, monkeypatch, public_reads):
    from datetime import UTC, datetime

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


@pytest.mark.parametrize("public_reads", [False, True])
def test_source_result_preview_excludes_sibling_outputs(tmp_path, monkeypatch, public_reads):
    from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryExtractionOutput
    from powercontext.builtin.inference import GenerationResult
    from powercontext.builtin.runtime import CaptureSource
    from powercontext.builtin.scope import ScopeDraft

    class SelectivePipeline(MemoryPipeline):
        async def generate(self, request, /):
            result = await super().generate(request)
            return GenerationResult(output=AtomicMemoryExtractionOutput(candidates=result.output.candidates[:1]))

    async def scenario():
        async with open_dream_runtime(
            BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/sources.db")),
            candidate_pipeline=atomic_memory_pipeline(SelectivePipeline()),
        ) as runtime:
            scope = (
                await runtime.scopes.create(
                    ScopeDraft(title="Batch attribution", summary="Exact evidence", idempotency_key="batch")
                )
            ).scope_id
            for source_id in ("has-fact", "no-candidate"):
                await runtime.sources.for_scope(scope).capture(
                    CaptureSource(source_id=source_id, content=f"Input {source_id}", metadata={})
                )
            await runtime.atomic_memory.for_scope(scope).flush()
            app = create_app(application=runtime)
            app.include_router(router, prefix="/dashboard")
            if not public_reads:
                app.state.dashboard_runtime = runtime
            if public_reads:
                monkeypatch.setattr("powercontext.server.dashboard.api._builtin_runtime", lambda request: None)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                history = (await client.get(f"/v1/scopes/{scope}/sources/content/no-candidate/processing")).json()
                assert len(history["items"][0]["results"]) == 1
                assert [
                    (ref["source_type"], ref["source_id"]) for ref in history["items"][0]["results"][0]["source_refs"]
                ] == [("content", "has-fact")]
                page = await client.get(
                    "/dashboard/processing", params={"scope": scope, "record_type": "sources", "lang": "en"}
                )
                assert page.status_code == 200, page.text
                rows = re.findall(r'<tr class="processing-source-row">(.*?)</tr>', page.text, re.S)
                assert len(rows) == 2
                for source_id in ("has-fact", "no-candidate"):
                    row = next(row for row in rows if f"Input {source_id}" in row)
                    assert ("The replay test passed without duplicate writes." in row) == (source_id == "has-fact")

    asyncio.run(scenario())
