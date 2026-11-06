# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Exercise HTML against real runtime operations and isolated databases."""

import re
from datetime import UTC, datetime
from html import unescape
from pathlib import Path
from secrets import token_urlsafe
from typing import Any, cast
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import insert

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import MODEL_USAGE_DAILY_TABLE
from powercontext.server.dashboard.api import DashboardAPI
from powercontext.server.dashboard.errors import ReadError
from powercontext.server.dashboard.navigation import collection_return
from powercontext.server.dashboard.preferences import CATALOGS
from powercontext.server.dashboard.routes import LABELS
from powercontext.server.factory import create_server_app
from powercontext.server.settings import (
    AccessControlConfig,
    BearerAuthConfig,
    DashboardConfig,
    McpConfig,
    ServerSettings,
)


@pytest.fixture
def dashboard(tmp_path: Path):
    token = token_urlsafe(24)
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/data.db"),
            mcp=McpConfig(enabled=False),
            dashboard=DashboardConfig(enabled=True),
            access=AccessControlConfig(mode="enforced"),
            auth=BearerAuthConfig(token=SecretStr(token)),
        ),
    )
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {token}"
        yield client


def test_dashboard_assets_revalidate_after_an_update(dashboard: TestClient) -> None:
    for path in ("layout.css", "vendor/htmx.min.js", "vendor/tabler.min.js"):
        response = dashboard.get(f"/dashboard/static/{path}")
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-cache"
        cached = dashboard.get(f"/dashboard/static/{path}", headers={"If-None-Match": response.headers["ETag"]})
        assert cached.status_code == 304
        assert cached.headers["Cache-Control"] == "no-cache"


def test_personal_dashboard_opens_without_models_or_saved_content(dashboard: TestClient) -> None:
    default = dashboard.get("/v1/scopes/default").json()
    home = dashboard.get("/")
    assert home.status_code == 200
    assert home.url.path == "/dashboard/home"
    assert f'value="{default["scope_id"]}" selected' in home.text
    for page in ("home", "notes", "methods", "handoff", "usage"):
        assert dashboard.get(f"/dashboard/{page}").status_code == 200
    saved = dashboard.post(
        "/v1/memory/remember",
        json={"scope_id": default["scope_id"], "kind": "fact", "text": "Use uv for dependency management."},
    )
    assert saved.status_code == 200
    assert "Use uv for dependency management." in dashboard.get("/dashboard/notes").text


def test_topic_dashboard_opens_without_content(dashboard: TestClient) -> None:
    topics = dashboard.get("/dashboard/topics", params={"lang": "en"})
    assert topics.status_code == 200
    assert "Topic Memory" in topics.text
    assert "Prompt configuration" not in topics.text
    assert "Artifacts" not in topics.text


def test_prompt_and_profile_dashboards_open_without_content(dashboard: TestClient) -> None:
    prompts = dashboard.get("/dashboard/prompts", params={"lang": "en"})
    profile = dashboard.get("/dashboard/profile", params={"lang": "en"})
    assert prompts.status_code == 200
    assert "Prompts" in prompts.text
    assert profile.status_code == 200
    assert "No profile in this scope" in profile.text
    assert "Prompts" in prompts.text


def test_dashboard_favicons_use_square_viewports(dashboard: TestClient) -> None:
    home = dashboard.get("/")
    icons = re.findall(r'<link rel="icon"[^>]*href="([^"]+)"', home.text)
    assert icons
    for link in icons:
        icon = dashboard.get(link)
        assert icon.status_code == 200
        assert icon.headers["content-type"].startswith("image/svg+xml")
        viewbox = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', icon.text)
        assert viewbox is not None
        assert float(viewbox[1]) == float(viewbox[2])


def create_scope(client: TestClient, title: str, parent: str | None = None) -> dict[str, Any]:
    result = client.post(
        "/v1/scopes",
        json={"title": title, "summary": f"Work on {title}", "parent_scope_id": parent, "idempotency_key": title},
    )
    assert result.status_code == 201
    return result.json()


@pytest.mark.parametrize("scope_selection", ["explicit", "default"])
def test_dashboard_audit_failure_does_not_expose_page_content(
    dashboard: TestClient, monkeypatch, scope_selection: str
) -> None:
    scope = create_scope(dashboard, "Audit protected scope title")
    params = {"scope": scope["scope_id"]} if scope_selection == "explicit" else {}
    if scope_selection == "default":
        selected = dashboard.put("/v1/scopes/default", json={"scope_id": scope["scope_id"]})
        assert selected.status_code == 200, selected.text
    saved = dashboard.post(
        "/v1/memory/remember",
        json={"scope_id": scope["scope_id"], "kind": "fact", "text": "Audit protected memory body."},
    )
    assert saved.status_code == 200, saved.text
    audit = cast(FastAPI, dashboard.app).state.access_control.audit

    async def unavailable(events):
        raise RuntimeError

    for page in ("home", "notes", "usage"):
        for headers in ({}, {"HX-Request": "true"}):
            with monkeypatch.context() as patch:
                patch.setattr(audit, "append_audits", unavailable)
                response = dashboard.get(f"/dashboard/{page}", params=params, headers=headers)
            assert response.status_code == 503, response.text
            assert scope["title"] not in response.text
            assert "Audit protected memory body." not in response.text
            recovered = dashboard.get(f"/dashboard/{page}", params=params, headers=headers)
            assert recovered.status_code == 200, recovered.text
            assert scope["title"] in recovered.text
            if page != "usage":
                assert "Audit protected memory body." in recovered.text


@pytest.mark.parametrize("page", ["home", "usage"])
def test_malformed_usage_preserves_authorized_page_sections(dashboard: TestClient, page: str) -> None:
    scope = create_scope(dashboard, "Readable scope with damaged usage")
    saved = dashboard.post(
        "/v1/memory/remember",
        json={"scope_id": scope["scope_id"], "kind": "fact", "text": "Unaffected content remains readable."},
    )
    assert saved.status_code == 200, saved.text
    runtime = cast(FastAPI, dashboard.app).state.application

    async def damage_usage():
        async with runtime.primary_database.transaction() as connection:
            await connection.execute(
                insert(MODEL_USAGE_DAILY_TABLE).values(
                    scope_id=scope["scope_id"],
                    usage_date=datetime.now(UTC).date(),
                    purpose="unknown-purpose",
                    operation="generation",
                    requests=1,
                    input_tokens=1,
                    output_tokens=1,
                    input_complete=True,
                    output_complete=True,
                )
            )

    assert dashboard.portal is not None
    dashboard.portal.call(damage_usage)
    response = dashboard.get(f"/dashboard/{page}", params={"scope": scope["scope_id"]})
    assert response.status_code == 200, response.text
    assert scope["title"] in response.text
    assert LABELS["error_503"] in response.text
    if page == "home":
        assert "Unaffected content remains readable." in response.text


def test_dashboard_preserves_injected_atomic_read_rules(dashboard: TestClient, monkeypatch) -> None:
    from powercontext.builtin.authz import AccessDeniedError
    from powercontext.builtin.runtime.application import BuiltinRuntime
    from powercontext.builtin.runtime.atomic_memory import AtomicMemoryAccess, AtomicMemoryApplication

    class DenyRead(AtomicMemoryAccess):
        async def authorize(self, connection, scope_id, context, action, ref=None):
            if action == "read":
                raise AccessDeniedError
            await super().authorize(connection, scope_id, context, action, ref)

    scope = create_scope(dashboard, "Injected memory access")
    saved = dashboard.post(
        "/v1/memory/remember",
        json={"scope_id": scope["scope_id"], "kind": "fact", "text": "Custom access protected memory."},
    )
    assert saved.status_code == 200, saved.text
    app = cast(FastAPI, dashboard.app)
    original = app.state.application
    atomic = AtomicMemoryApplication(
        original.primary_database, original._records()._artifacts, original.atomic_memory._application.index
    )
    atomic.security = DenyRead()
    atomic.service.shared.security = atomic.security
    injected = BuiltinRuntime(
        provider=original._provider,
        capabilities=original._capabilities,
        primary_database=original.primary_database,
        atomic_memory_application=atomic,
        record_service=original._records(),
        scope_application=original.scopes,
    )
    try:
        with monkeypatch.context() as patch:
            patch.setattr(app.state, "application", injected)
            denied = dashboard.post("/v1/atomic-memory/list", json={"scope_id": scope["scope_id"]})
            assert denied.status_code == 403, denied.text
            page = dashboard.get("/dashboard/notes", params={"scope": scope["scope_id"]})
            assert page.status_code == 200, page.text
            assert "Custom access protected memory." not in page.text
            assert LABELS["error_403"] in page.text
    finally:
        assert dashboard.portal is not None
        dashboard.portal.call(injected.close)


def capture_source(client: TestClient, scope: str, content: str) -> dict[str, Any]:
    result = client.post("/v1/sources/content", json={"scope_id": scope, "source_id": "evidence", "content": content})
    assert result.is_success
    return result.json()["source"]


def create_experience(client: TestClient, scope: str, source: dict[str, Any], situation: str, lesson: str) -> str:
    proposal = {
        "situation": situation,
        "action": "Reuse the artifact search API",
        "outcome": "Matching experiences return",
        "lesson": lesson,
    }
    candidate = client.post(
        "/v1/experience/propose",
        json={"scope_id": scope, "proposal": proposal, "source_refs": [source], "artifact_refs": []},
    )
    assert candidate.status_code == 201
    body = candidate.json()
    approved = client.post(
        "/v1/candidates/approve",
        json={"scope_id": scope, "candidate_id": body["candidate_id"], "expected_version": body["version"]},
    )
    assert approved.status_code == 200
    return approved.json()["result_artifact"]["artifact_id"]


def create_skill(client: TestClient, scope: str, source: dict[str, Any], name: str) -> str:
    proposal = {
        "name": name,
        "description": "Review the report",
        "instructions": "Keep the documented limits.",
        "validation": ["Keep the documented limits."],
    }
    candidate = client.post(
        "/v1/skill/propose",
        json={"scope_id": scope, "proposal": proposal, "source_refs": [source], "artifact_refs": []},
    )
    assert candidate.status_code == 201
    body = candidate.json()
    approved = client.post(
        "/v1/candidates/approve",
        json={"scope_id": scope, "candidate_id": body["candidate_id"], "expected_version": body["version"]},
    )
    assert approved.status_code == 200
    return approved.json()["result_artifact"]["artifact_id"]


def labelled_link(html: str, label: str) -> str:
    """Return the first link whose text contains ``label`` (e.g. a back arrow)."""

    match = re.search(r'<a\b[^>]*href="([^"]+)"[^>]*>[^<]*' + re.escape(label) + r"[^<]*</a>", html)
    assert match is not None
    return unescape(match[1])


def detail_link(html: str, family: str) -> str:
    return next(
        unescape(value)
        for value in re.findall(r'href="([^"]+)"', html)
        if urlsplit(unescape(value)).path == f"/dashboard/{family}"
    )


def alert_retry(html: str) -> str:
    alert = re.search(r'<section class="alert alert-warning my-3" role="alert">.*?</section>', html, re.S)
    assert alert is not None
    match = re.search(r'href="([^"]+)"[^>]*>' + re.escape(LABELS["retry"]) + "</a>", alert[0])
    assert match is not None
    return unescape(match[1])


def test_scope_selection_default_and_unknown_scope(dashboard: TestClient) -> None:
    parent = create_scope(dashboard, "Project")
    child = create_scope(dashboard, "Documentation", parent["scope_id"])
    assert dashboard.put("/v1/scopes/default", json={"scope_id": child["scope_id"]}).status_code == 200
    result = dashboard.get("/dashboard/home")
    assert result.status_code == 200
    assert f'value="{child["scope_id"]}" selected' in result.text
    assert "Work on Documentation" in result.text
    assert "Work on Project" not in result.text
    result = dashboard.get("/dashboard/home", params={"scope": parent["scope_id"]})
    assert "Work on Project" in result.text
    assert dashboard.get("/v1/scopes/default").json()["scope_id"] == child["scope_id"]
    assert dashboard.get("/dashboard/home?scope=missing").status_code == 404
    assert LABELS["entry_heading"] in dashboard.get("/dashboard/home?scope=").text


def test_scope_picker_discovers_all_pages_without_loading_the_catalog_in_ssr(dashboard: TestClient) -> None:
    scopes = [create_scope(dashboard, f"Picker scope {index:02}") for index in range(30)]
    selected = scopes[-1]
    page = dashboard.get("/dashboard/notes", params={"scope": selected["scope_id"]})
    assert page.status_code == 200
    assert selected["title"] in page.text
    assert scopes[0]["title"] not in page.text
    seen = []
    cursor = None
    while True:
        params = {"query": "Picker scope"}
        if cursor:
            params["cursor"] = cursor
        response = dashboard.get("/dashboard/scope-options", params=params)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        body = response.json()
        assert len(body["items"]) <= 25
        seen.extend(item["scope_id"] for item in body["items"])
        assert all("summary" not in item and "external_references" not in item for item in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == 30
    assert set(seen) == {scope["scope_id"] for scope in scopes}
    result = dashboard.get("/dashboard/scope-options", params={"query": selected["title"]})
    assert [item["scope_id"] for item in result.json()["items"]] == [selected["scope_id"]]
    assert dashboard.get("/dashboard/scope-options?query=" + "x" * 257).status_code == 422


def test_language_survives_navigation_without_translating_records(dashboard: TestClient) -> None:
    scope = create_scope(dashboard, "Release work")
    dashboard.put("/v1/scopes/default", json={"scope_id": scope["scope_id"]})
    response = dashboard.get("/dashboard/home", params={"lang": "en", "scope": scope["scope_id"], "period": "30d"})
    assert response.status_code == 200
    assert '<html lang="en">' in response.text
    assert "Experiences &amp; Skills" in response.text
    assert "Work on Release work" in response.text
    chinese_link = re.search(r'href="([^"]+)" lang="zh-CN"', response.text)
    assert chinese_link is not None
    changed = dashboard.get(unescape(chinese_link[1]))
    assert changed.status_code == 200
    assert changed.url.params["scope"] == scope["scope_id"]
    assert changed.url.params["period"] == "30d"
    assert '<html lang="zh-CN">' in changed.text
    dashboard.get("/dashboard/home?lang=en")
    partial = dashboard.get("/dashboard/notes", headers={"HX-Request": "true"})
    assert "Memories" in partial.text
    assert "Release work" in partial.text
    assert '<html lang="zh-CN">' in dashboard.get("/dashboard/home?lang=zh").text
    assert '<html lang="zh-CN">' in dashboard.get("/dashboard/home?lang=invalid").text


def page_link(html: str, label: str) -> str:
    match = re.search(r'<a\b[^>]*href="([^"]+)"[^>]*>' + re.escape(label) + "</a>", html)
    assert match is not None
    return unescape(match[1])


def record_links(html: str, route: str, identity: str) -> set[str]:
    links = [urlsplit(unescape(value)) for value in re.findall(r'href="([^"]+)"', html)]
    return {value for link in links if link.path == route for value in parse_qs(link.query).get(identity, [])}


def collect_pages(client: TestClient, first: str, route: str, identity: str) -> set[str]:
    current = first
    seen: set[str] = set()
    visited: set[str] = set()
    while True:
        records = record_links(current, route, identity)
        assert records and not records.intersection(seen)
        seen.update(records)
        match = re.search(r'href="([^"<>]+)">' + re.escape(LABELS["next_page"]) + "</a>", current)
        if match is None:
            return seen
        following = unescape(match[1])
        assert following not in visited
        visited.add(following)
        next_page = client.get(following)
        assert next_page.status_code == 200
        previous = client.get(page_link(next_page.text, LABELS["previous_page"]))
        assert previous.status_code == 200
        assert record_links(previous.text, route, identity) == records
        current = next_page.text


def test_topic_pagination_keeps_every_record_readable(dashboard: TestClient) -> None:
    scope = create_scope(dashboard, "Long topic directory")["scope_id"]
    expected = set()
    for index in range(51):
        response = dashboard.post(
            f"/v1/scopes/{scope}/artifacts",
            json={
                "family": "topic-memory",
                "content": {
                    "title": f"Topic {index}",
                    "summary": "Saved topic summary.",
                    "detail": f"Saved topic detail {index}.",
                },
            },
        )
        assert response.status_code == 201
        expected.add(response.json()["artifact_id"])
    first = dashboard.get("/dashboard/topics", params={"scope": scope})
    assert first.status_code == 200
    assert collect_pages(dashboard, first.text, "/dashboard/topics", "topic_artifact") == expected


def test_memory_pagination_and_deep_link_select_the_corresponding_text(dashboard: TestClient) -> None:
    scope = create_scope(dashboard, "Long memory")["scope_id"]
    for index in range(75):
        response = dashboard.post(
            "/v1/memory/remember",
            json={"scope_id": scope, "kind": "constraint", "text": f"Recorded constraint {index}."},
        )
        assert response.status_code == 200
    entries = dashboard.post("/v1/atomic-memory/list", json={"scope_id": scope, "limit": 100}).json()["items"]
    first = dashboard.get("/dashboard/notes", params={"scope": scope})
    expected = {item["artifact"]["artifact_id"] for item in entries}
    assert collect_pages(dashboard, first.text, "/dashboard/notes", "artifact") == expected
    entry = entries[-1]["artifact"]
    deep = dashboard.get(
        "/dashboard/notes", params={"scope": scope, "artifact": entry["artifact_id"], "revision": entry["revision"]}
    )
    assert deep.status_code == 200
    assert f"atomic-memory/{entry['artifact_id']}@{entry['revision']}" in deep.text
    assert entries[-1]["text"] in deep.text
    assert dashboard.get("/dashboard/notes", params={"scope": scope, "notes_page": "invalid"}).status_code == 422
    assert dashboard.get("/dashboard/notes", params={"scope": scope, "notes_history": "invalid"}).status_code == 422


def test_memory_search_preserves_scope_references_and_result_pagination(dashboard: TestClient) -> None:
    scope = create_scope(dashboard, "Searchable memory")["scope_id"]
    other = create_scope(dashboard, "Separate memory")["scope_id"]
    for index in range(9):
        response = dashboard.post(
            "/v1/memory/remember",
            json={"scope_id": scope, "kind": "constraint", "text": f"Release checklist item {index}."},
        )
        assert response.status_code == 200
    response = dashboard.post(
        "/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": "Invoices use euros."}
    )
    assert response.status_code == 200
    hits = dashboard.post(
        "/v1/atomic-memory/search", json={"scope_id": scope, "query": "Release", "mode": "text", "limit": 50}
    ).json()["hits"]
    assert hits
    first = dashboard.get("/dashboard/notes", params={"scope": scope, "q": "Release"})
    assert first.status_code == 200
    assert LABELS["constraint"] in first.text
    assert f"{hits[0]['score']:.6f}" in first.text
    assert 'data-memory-channel="text"' in first.text
    assert LABELS["page_number"].format(page=1) in first.text
    second = dashboard.get(page_link(first.text, LABELS["next_page"]))
    assert second.status_code == 200
    assert second.url.params["q"] == "Release"
    assert second.url.params["scope"] == scope
    assert LABELS["page_number"].format(page=2) in second.text
    previous = dashboard.get(page_link(second.text, LABELS["previous_page"]))
    assert record_links(previous.text, "/dashboard/notes", "artifact") == record_links(
        first.text, "/dashboard/notes", "artifact"
    )
    assert collect_pages(dashboard, first.text, "/dashboard/notes", "artifact") == {
        hit["memory"]["artifact"]["artifact_id"] for hit in hits
    }
    for hit in hits:
        artifact = hit["memory"]["artifact"]
        selected = dashboard.get(
            "/dashboard/notes",
            params={
                "scope": scope,
                "q": "Release",
                "artifact": artifact["artifact_id"],
                "revision": artifact["revision"],
            },
        )
        assert selected.status_code == 200
        assert hit["memory"]["text"] in selected.text
    for target, query in [(other, "Release"), (scope, "nonexistent")]:
        empty = dashboard.get("/dashboard/notes", params={"scope": target, "q": query})
        assert empty.status_code == 200
        assert LABELS["notes_no_match"] in empty.text
        assert LABELS["page_number"].format(page=1) in empty.text
        assert not record_links(empty.text, "/dashboard/notes", "artifact")
        assert LABELS["memory_requested_mode"] in empty.text
        assert f"{LABELS['memory_used_mode']}: {LABELS['memory_mode_text']}" in empty.text
    single = dashboard.get("/dashboard/notes", params={"scope": scope, "q": "Invoices"})
    assert LABELS["page_number"].format(page=1) in single.text
    assert LABELS["previous_page"] in single.text
    assert LABELS["next_page"] in single.text
    restored = dashboard.get(page_link(first.text, LABELS["clear_search"]))
    assert restored.status_code == 200
    assert not restored.url.params.get("q")
    expected = dashboard.post("/v1/atomic-memory/list", json={"scope_id": scope}).json()["items"]
    assert collect_pages(dashboard, restored.text, "/dashboard/notes", "artifact") == {
        item["artifact"]["artifact_id"] for item in expected
    }


def test_memory_search_modes_surface_semantic_hits_and_keep_navigation(tmp_path: Path) -> None:
    from powercontext.builtin.artifacts.memory import EmbeddingProfile
    from powercontext.builtin.inference import EmbeddingResult

    class CoffeeEmbedding:
        profile = EmbeddingProfile(
            profile_id="dashboard-coffee", model="test", dimension=3, distance="l2", normalization="unit"
        )

        async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
            return EmbeddingResult(
                vectors=tuple(
                    (1.0, 0.0, 0.0) if any(word in text.lower() for word in ("coffee", "caffeine")) else (0.0, 1.0, 0.0)
                    for text in texts
                )
            )

    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/semantic.db"),
            dashboard=DashboardConfig(enabled=True),
            mcp=McpConfig(enabled=False),
        ),
        embedding_model=CoffeeEmbedding(),
    )
    with TestClient(app) as client:
        scope = create_scope(client, "Coffee preferences")["scope_id"]
        for index in range(8):
            assert (
                client.post(
                    "/v1/memory/remember",
                    json={"scope_id": scope, "kind": "fact", "text": f"Coffee preparation choice {index}."},
                ).status_code
                == 200
            )
        lexical = client.get("/dashboard/notes", params={"scope": scope, "q": "caffeine", "mode": "fts"})
        assert lexical.status_code == 200
        assert not record_links(lexical.text, "/dashboard/notes", "artifact")
        semantic = client.get("/dashboard/notes", params={"scope": scope, "q": "caffeine", "mode": "vector"})
        assert semantic.status_code == 200
        assert len(record_links(semantic.text, "/dashboard/notes", "artifact")) == 6
        assert 'data-memory-channel="vector"' in semantic.text
        assert 'data-memory-channel="text"' not in semantic.text
        following = client.get(page_link(semantic.text, LABELS["next_page"]))
        assert following.url.params["mode"] == "vector"
        assert len(record_links(following.text, "/dashboard/notes", "artifact")) == 2
        selected_url = next(
            unescape(url)
            for url in re.findall(r'href="([^"]+)"', following.text)
            if "artifact=" in unescape(url) and "/dashboard/notes?" in unescape(url)
        )
        selected = client.get(selected_url)
        assert selected.status_code == 200
        assert selected.url.params["mode"] == "vector"
        assert 'data-memory-channel="vector"' in selected.text.split('id="memory-accordion"', 1)[1]
        restored = client.get(page_link(selected.text, LABELS["clear_search"]))
        assert restored.url.params["mode"] == "vector"
        assert not restored.url.params.get("artifact")
        assert "memory-search-evidence" not in restored.text
        forgotten = client.get(page_link(restored.text, LABELS["memory_state_forgotten"]))
        assert forgotten.url.params["mode"] == "vector"
        assert forgotten.url.params["note_state"] == "forgotten"
        automatic = client.get("/dashboard/notes", params={"scope": scope, "q": "coffee", "lang": "en"})
        assert "Executed mode: Hybrid" in automatic.text
        assert 'data-memory-channel="text"' in automatic.text
        assert 'data-memory-channel="vector"' in automatic.text
        assert "0.032787" in automatic.text
        invalid = client.get(
            "/dashboard/notes", params={"scope": scope, "q": "coffee", "mode": "keyword", "lang": "zh"}
        )
        assert LABELS["error_422"] in invalid.text
        assert 'class="text-secondary memory-search-mode"' not in invalid.text


def test_collection_errors_return_to_a_readable_list(dashboard: TestClient) -> None:
    scope = create_scope(dashboard, "Recoverable collections")["scope_id"]
    for page, selection, empty_label in [
        ("notes", {"notes_page": "99"}, "notes_empty"),
        ("notes", {"notes_page": "invalid"}, "notes_empty"),
        ("methods", {"kind": "skill", "skill_page": "99"}, "skills_empty"),
        ("methods", {"kind": "experience", "experience_history": "invalid"}, "experience_empty"),
        ("handoff", {"handoff_history": "invalid"}, "handoff_empty"),
    ]:
        response = dashboard.get(f"/dashboard/{page}", params={"scope": scope, "period": "30d", **selection})
        assert LABELS["back_list"] in response.text
        restored = dashboard.get(page_link(response.text, LABELS["back_list"]))
        assert restored.status_code == 200
        assert restored.url.params["scope"] == scope
        assert restored.url.params["period"] == "30d"
        assert LABELS[empty_label] in restored.text


@pytest.mark.parametrize("family", ["experience", "skill"])
def test_method_pagination_returns_to_the_previous_records(dashboard: TestClient, family: str) -> None:
    scope = create_scope(dashboard, "Long library")["scope_id"]
    source = dashboard.post(
        "/v1/sources/content",
        json={
            "scope_id": scope,
            "source_id": "review",
            "content": "Check the saved report and preserve its limits.",
        },
    ).json()["source"]
    expected = set()
    for index in range(19):
        proposal = (
            {
                "situation": "Review",
                "action": "Read the report",
                "outcome": "Unknown",
                "lesson": f"Review lesson {index}",
            }
            if family == "experience"
            else {
                "name": f"review-{index}",
                "description": "Read the report",
                "instructions": "Preserve its limits.",
                "validation": ["Keep unknown outcomes."],
            }
        )
        result = dashboard.post(
            f"/v1/{family}/propose",
            json={"scope_id": scope, "proposal": proposal, "source_refs": [source], "artifact_refs": []},
        )
        assert result.status_code == 201
        candidate = result.json()
        approved = dashboard.post(
            "/v1/candidates/approve",
            json={
                "scope_id": scope,
                "candidate_id": candidate["candidate_id"],
                "expected_version": candidate["version"],
            },
        )
        assert approved.status_code == 200
        expected.add(approved.json()["result_artifact"]["artifact_id"])
    first = dashboard.get("/dashboard/methods", params={"scope": scope, "kind": family})
    assert collect_pages(dashboard, first.text, f"/dashboard/{family}", "artifact") == expected


@pytest.mark.parametrize("language", ["zh", "en"])
def test_experience_search_filters_results_and_restores_browsing(dashboard: TestClient, language: str) -> None:
    labels = CATALOGS[language]
    scope = create_scope(dashboard, f"Experience search {language}")["scope_id"]
    source = capture_source(dashboard, scope, "Search the dashboard experiences.")
    create_experience(
        dashboard, scope, source, situation="Adding dashboard search", lesson="Deploy the dashboard search form"
    )
    create_experience(
        dashboard, scope, source, situation="Rotating the signing key", lesson="Never reuse a retired signing key"
    )

    browsing = dashboard.get("/dashboard/methods", params={"scope": scope, "kind": "experience", "lang": language})
    assert browsing.status_code == 200
    assert labels["search_experiences"] in browsing.text
    assert "Deploy the dashboard search form" in browsing.text
    assert "Never reuse a retired signing key" in browsing.text

    searched = dashboard.get(
        "/dashboard/methods", params={"scope": scope, "kind": "experience", "q": "dashboard", "lang": language}
    )
    assert searched.status_code == 200
    assert 'value="dashboard"' in searched.text
    assert "Deploy the dashboard search form" in searched.text
    assert "Never reuse a retired signing key" not in searched.text

    cleared = dashboard.get(page_link(searched.text, labels["clear_search"]))
    assert cleared.status_code == 200
    assert cleared.url.params["kind"] == "experience"
    assert "Never reuse a retired signing key" in cleared.text
    assert 'value="dashboard"' not in cleared.text

    empty = dashboard.get(
        "/dashboard/methods",
        params={"scope": scope, "kind": "experience", "q": "absent-token", "lang": language},
    )
    assert empty.status_code == 200
    assert labels["experiences_no_match"] in empty.text
    assert labels["experience_empty"] not in empty.text


def test_experience_search_preserves_query_and_page_through_detail_and_back(dashboard: TestClient) -> None:
    scope = create_scope(dashboard, "Experience search paging")["scope_id"]
    source = capture_source(dashboard, scope, "Page through the matching experiences.")
    for index in range(7):
        create_experience(
            dashboard,
            scope,
            source,
            situation=f"Reviewing needle {index}",
            lesson=f"Keep needle note {index} documented",
        )
    first = dashboard.get("/dashboard/methods", params={"scope": scope, "kind": "experience", "q": "needle"})
    assert first.status_code == 200
    assert len(record_links(first.text, "/dashboard/experience", "artifact")) == 6
    following = page_link(first.text, LABELS["next_page"])
    assert parse_qs(urlsplit(following).query)["q"] == ["needle"]
    second = dashboard.get(following)
    assert second.status_code == 200
    page_records = record_links(second.text, "/dashboard/experience", "artifact")
    assert len(page_records) == 1
    detail_url = detail_link(second.text, "experience")
    return_to = parse_qs(urlsplit(detail_url).query)["return_to"][0]
    assert parse_qs(urlsplit(return_to).query)["q"] == ["needle"]
    assert parse_qs(urlsplit(return_to).query)["experience_page"] == ["2"]
    detail = dashboard.get(detail_url)
    assert detail.status_code == 200
    restored = dashboard.get(labelled_link(detail.text, LABELS["back_methods"]))
    assert restored.status_code == 200
    assert restored.url.params["q"] == "needle"
    assert restored.url.params["experience_page"] == "2"
    assert restored.url.params["kind"] == "experience"
    assert record_links(restored.text, "/dashboard/experience", "artifact") == page_records


def test_skill_search_preserves_the_result_page_through_detail_and_back(dashboard: TestClient) -> None:
    scope = create_scope(dashboard, "Skill search paging")["scope_id"]
    source = capture_source(dashboard, scope, "Page through the matching skills.")
    for index in range(7):
        create_skill(dashboard, scope, source, f"needle-skill-{index}")
    second = dashboard.get(
        "/dashboard/methods", params={"scope": scope, "kind": "skill", "q": "needle", "skill_page": "2"}
    )
    assert second.status_code == 200
    page_records = record_links(second.text, "/dashboard/skill", "artifact")
    assert len(page_records) == 1
    detail_url = detail_link(second.text, "skill")
    detail = dashboard.get(detail_url)
    assert detail.status_code == 200
    restored = dashboard.get(labelled_link(detail.text, LABELS["back_methods"]))
    assert restored.status_code == 200
    assert restored.url.params["q"] == "needle"
    assert restored.url.params["skill_page"] == "2"
    assert restored.url.params["kind"] == "skill"
    assert record_links(restored.text, "/dashboard/skill", "artifact") == page_records


@pytest.mark.parametrize("family", ["experience", "skill"])
def test_methods_return_uses_the_current_language(dashboard: TestClient, family: str) -> None:
    scope = create_scope(dashboard, f"{family} language return")["scope_id"]
    source = capture_source(dashboard, scope, f"Keep the {family} language searchable.")
    if family == "experience":
        create_experience(dashboard, scope, source, situation="needle situation", lesson="Keep the needle lesson")
    else:
        create_skill(dashboard, scope, source, "needle-skill")
    searched = dashboard.get("/dashboard/methods", params={"scope": scope, "kind": family, "q": "needle", "lang": "en"})
    assert searched.status_code == 200
    detail_url = detail_link(searched.text, family)
    return_to = parse_qs(urlsplit(detail_url).query)["return_to"][0]
    assert parse_qs(urlsplit(return_to).query)["lang"] == ["en"]
    detail = dashboard.get(detail_url)
    assert detail.status_code == 200
    switched = dashboard.get(f"{detail_url}&lang=zh")
    assert switched.status_code == 200
    restored = dashboard.get(labelled_link(switched.text, LABELS["back_methods"]))
    assert restored.status_code == 200
    assert restored.url.params["lang"] == "zh"
    assert restored.url.params["q"] == "needle"
    assert restored.url.params["kind"] == family
    assert ("搜索经验" if family == "experience" else "搜索技能") in restored.text


def test_methods_return_to_accepts_supported_text() -> None:
    scope = "scp_demo"
    for query in ("needle\\path", "needle & path = 100% #1", "中" * 2000):
        raw = "/dashboard/methods?" + urlencode({
            "scope": scope,
            "period": "7d",
            "kind": "experience",
            "q": query,
            "experience_page": "2",
        })
        result = collection_return(raw, scope, page="methods")
        assert result is not None
        params = parse_qs(urlsplit(result).query)
        assert params["q"] == [query]
        assert params["experience_page"] == ["2"]


@pytest.mark.parametrize(
    "raw",
    [
        "/dashboard/methods?scope=other&kind=experience&q=needle",
        "/dashboard/handoff?scope=scp_demo&cursor=next",
        "/dashboard/methods?scope=scp_demo&kind=all&q=needle",
        "/dashboard/methods?scope=scp_demo&kind=experience&q=needle&experience_page=1%5C2",
        "/dashboard/methods?scope=scp_demo&kind=experience&q=needle%0D%0ASet-Cookie",
    ],
)
def test_methods_return_to_rejects_unsupported_context(raw: str) -> None:
    assert collection_return(raw, "scp_demo", page="methods") is None


def test_methods_return_to_rejects_oversized_context() -> None:
    raw = "/dashboard/methods?scope=scp_demo&kind=experience&q=" + "a" * (33 * 1024)
    assert collection_return(raw, "scp_demo", page="methods") is None


@pytest.mark.parametrize(
    ("query", "text"),
    [
        ("needle\\path", "Keep the needle path"),
        ("needle & path = 100% #1", "Keep special characters"),
        ("经验 " * 460, "经验 long query"),
    ],
    ids=["backslash", "special", "long"],
)
def test_experience_search_accepts_edge_query_text(dashboard: TestClient, query: str, text: str) -> None:
    scope = create_scope(dashboard, "Experience edge queries")["scope_id"]
    source = capture_source(dashboard, scope, "Keep edge queries searchable.")
    create_experience(dashboard, scope, source, situation="needle path", lesson="Keep the needle path")
    create_experience(dashboard, scope, source, situation="needle & path = 100% #1", lesson="Keep special characters")
    create_experience(dashboard, scope, source, situation="经验 long query", lesson="经验 " * 460)
    searched = dashboard.get("/dashboard/methods", params={"scope": scope, "kind": "experience", "q": query})
    assert searched.status_code == 200
    assert text in searched.text
    detail_url = detail_link(searched.text, "experience")
    detail = dashboard.get(detail_url)
    assert detail.status_code == 200
    restored = dashboard.get(labelled_link(detail.text, LABELS["back_methods"]))
    assert restored.status_code == 200
    assert restored.url.params["q"] == query


def test_experience_detail_rejects_a_foreign_return_to(dashboard: TestClient) -> None:
    scope = create_scope(dashboard, "Experience return boundary")["scope_id"]
    other = create_scope(dashboard, "Other experience return")["scope_id"]
    source = capture_source(dashboard, scope, "Keep the return inside its scope.")
    artifact = create_experience(dashboard, scope, source, situation="Reviewing", lesson="Stay in the scope")
    detail = dashboard.get(
        "/dashboard/experience",
        params={
            "scope": scope,
            "artifact": artifact,
            "revision": "1",
            "return_to": f"/dashboard/methods?scope={other}&kind=experience&q=needle",
        },
    )
    assert detail.status_code == 200
    restored = dashboard.get(labelled_link(detail.text, LABELS["back_methods"]))
    assert restored.status_code == 200
    assert restored.url.params["scope"] == scope
    assert "q" not in restored.url.params


def test_experience_search_state_survives_compare_and_rollback(dashboard: TestClient) -> None:
    scope = create_scope(dashboard, "Experience search history")["scope_id"]
    content = {
        "situation": "Reviewing the needle search",
        "action": "Keep the search origin",
        "outcome": "The result page survives detail navigation",
        "lesson": "Keep the needle search state",
    }
    created = dashboard.post(f"/v1/scopes/{scope}/artifacts", json={"family": "experience", "content": content})
    assert created.status_code == 201, created.text
    artifact = created.json()["artifact_id"]
    current = dashboard.get(f"/v1/scopes/{scope}/artifacts/experience/{artifact}")
    revised = dashboard.put(
        f"/v1/scopes/{scope}/artifacts/experience/{artifact}",
        json={"content": {**content, "lesson": "A later needle lesson"}},
        headers={"If-Match": current.headers["etag"]},
    )
    assert revised.status_code == 200, revised.text

    searched = dashboard.get("/dashboard/methods", params={"scope": scope, "kind": "experience", "q": "needle"})
    assert searched.status_code == 200
    detail_url = detail_link(searched.text, "experience")
    return_to = parse_qs(urlsplit(detail_url).query)["return_to"][0]
    revision = dashboard.get(
        "/dashboard/experience",
        params={
            "scope": scope,
            "artifact": artifact,
            "revision": "1",
            "compare": "2",
            "return_to": return_to,
        },
    )
    assert revision.status_code == 200, revision.text
    assert 'action="/dashboard/rollback"' in revision.text
    compare_form = re.search(
        r'<form class="card-body border-top" method="get" action="/dashboard/experience">.*?</form>',
        revision.text,
        re.S,
    )
    assert compare_form is not None
    assert 'name="return_to"' in compare_form[0]
    rollback_form = re.search(
        r'<form class="card-body border-top" method="post" action="/dashboard/rollback">.*?</form>', revision.text, re.S
    )
    assert rollback_form is not None
    assert 'name="return_to"' in rollback_form[0]

    restored = dashboard.post(
        "/dashboard/rollback",
        data={
            "scope": scope,
            "family": "experience",
            "artifact_id": artifact,
            "source_revision": "1",
            "expected_revision": "2",
            "reason": "Restore the searched revision",
            "return_to": return_to,
        },
        headers={"Origin": "http://testserver"},
    )
    assert restored.status_code == 200, restored.text
    assert restored.url.params["return_to"] == return_to
    back = dashboard.get(labelled_link(restored.text, LABELS["back_methods"]))
    assert back.status_code == 200
    assert back.url.params["q"] == "needle"
    assert back.url.params["kind"] == "experience"


def test_experience_search_retry_keeps_the_result_page(dashboard: TestClient, monkeypatch) -> None:
    scope = create_scope(dashboard, "Experience retry page")["scope_id"]
    search_path = f"/v1/scopes/{scope}/artifacts/experience/search"
    read = DashboardAPI.read

    async def failing(api, path, payload=None):
        if path == search_path:
            raise ReadError(503, "service_unavailable")
        return await read(api, path, payload)

    monkeypatch.setattr(DashboardAPI, "read", failing)
    response = dashboard.get(
        "/dashboard/methods",
        params={"scope": scope, "kind": "experience", "q": "needle", "experience_page": "2"},
    )
    assert response.status_code == 200
    retry = alert_retry(response.text)
    params = parse_qs(urlsplit(retry).query)
    assert params["q"] == ["needle"]
    assert params["experience_page"] == ["2"]
    assert params["kind"] == ["experience"]
    form = re.search(r'<form action="/dashboard/methods".*?</form>', response.text, re.S)
    assert form is not None
    assert 'name="experience_page"' not in form[0]


def test_out_of_range_result_page_retry_returns_to_the_first_page(dashboard: TestClient) -> None:
    scope = create_scope(dashboard, "Experience page range")["scope_id"]
    source = capture_source(dashboard, scope, "Keep the page range searchable.")
    create_experience(dashboard, scope, source, situation="needle range", lesson="Keep the needle range")
    response = dashboard.get(
        "/dashboard/methods",
        params={"scope": scope, "kind": "experience", "q": "needle", "experience_page": "99"},
    )
    assert response.status_code == 200
    retry = alert_retry(response.text)
    params = parse_qs(urlsplit(retry).query)
    assert params["q"] == ["needle"]
    assert "experience_page" not in params
    restored = dashboard.get(retry)
    assert restored.status_code == 200
    assert "Keep the needle range" in restored.text


def test_experience_search_reports_request_failures_distinctly(dashboard: TestClient, monkeypatch) -> None:
    scope = create_scope(dashboard, "Experience search failure")["scope_id"]
    source = capture_source(dashboard, scope, "Search failures stay separate from empty results.")
    create_experience(dashboard, scope, source, situation="Losing the search backend", lesson="Surface a read failure")
    read = DashboardAPI.read
    search_path = f"/v1/scopes/{scope}/artifacts/experience/search"
    seen: list[str] = []

    async def failing(api, path, payload=None):
        if path == search_path:
            seen.append(path)
            raise ReadError(503, "service_unavailable")
        return await read(api, path, payload)

    monkeypatch.setattr(DashboardAPI, "read", failing)
    response = dashboard.get("/dashboard/methods", params={"scope": scope, "kind": "experience", "q": "search"})
    assert response.status_code == 200
    assert seen == [search_path]
    assert LABELS["error_503"] in response.text
    assert LABELS["experiences_no_match"] not in response.text


def test_experience_detail_error_returns_to_the_search_page(dashboard: TestClient, monkeypatch) -> None:
    scope = create_scope(dashboard, "Experience detail error")["scope_id"]
    source = capture_source(dashboard, scope, "Keep the needle error searchable.")
    create_experience(dashboard, scope, source, situation="needle error", lesson="Keep the needle error")
    searched = dashboard.get("/dashboard/methods", params={"scope": scope, "kind": "experience", "q": "needle"})
    assert searched.status_code == 200
    detail_url = detail_link(searched.text, "experience")
    read = DashboardAPI.read

    async def failing(api, path, payload=None):
        if path.startswith(f"/v1/scopes/{scope}/artifacts/experience/"):
            raise ReadError(503, "service_unavailable")
        return await read(api, path, payload)

    monkeypatch.setattr(DashboardAPI, "read", failing)
    detail = dashboard.get(detail_url)
    assert detail.status_code == 503
    assert LABELS["error_503"] in detail.text
    restored = dashboard.get(page_link(detail.text, LABELS["back_list"]))
    assert restored.status_code == 200
    assert restored.url.params["q"] == "needle"


def test_skill_with_usage_provenance_is_readable_and_searchable(dashboard: TestClient) -> None:
    scope = create_scope(dashboard, "Skill review")["scope_id"]
    source = dashboard.post(
        "/v1/sources/content",
        json={
            "scope_id": scope,
            "source_id": "report",
            "content": "The report leaves unsupported outcomes unknown.",
        },
    ).json()["source"]
    proposal = {
        "name": "review-report",
        "description": "Review the report",
        "instructions": "Read the report.",
        "validation": ["Keep unsupported outcomes unknown."],
    }
    candidate = dashboard.post(
        "/v1/skill/propose",
        json={
            "scope_id": scope,
            "proposal": proposal,
            "source_refs": [source],
            "artifact_refs": [],
        },
    ).json()
    approved = dashboard.post(
        "/v1/candidates/approve",
        json={
            "scope_id": scope,
            "candidate_id": candidate["candidate_id"],
            "expected_version": candidate["version"],
        },
    ).json()["result_artifact"]
    usage = dashboard.post(
        "/v1/skill/usage",
        json={
            "scope_id": scope,
            "observation_id": "review-observation",
            "skill_ref": approved,
            "package_digest": "sha256:" + candidate["proposal"]["package"]["tree_digest"],
            "target_id": "review-consumer",
            "selected": True,
            "invoked": "true",
            "validation": "unknown",
            "outcome": "unknown",
        },
    )
    assert usage.status_code == 201
    candidate = dashboard.post(
        "/v1/skill/propose",
        json={
            "scope_id": scope,
            "proposal": {**proposal, "instructions": "Read the report and retain unknown outcomes."},
            "source_refs": [usage.json()["source"], source],
            "artifact_refs": [approved],
            "target": approved,
        },
    ).json()
    updated = dashboard.post(
        "/v1/candidates/approve",
        json={
            "scope_id": scope,
            "candidate_id": candidate["candidate_id"],
            "expected_version": candidate["version"],
        },
    ).json()["result_artifact"]
    collection = dashboard.get("/dashboard/methods", params={"scope": scope, "kind": "skill", "q": "report"})
    assert collection.status_code == 200
    assert "review-report" in collection.text
    detail = dashboard.get(
        "/dashboard/skill", params={"scope": scope, "artifact": updated["artifact_id"], "revision": updated["revision"]}
    )
    assert detail.status_code == 200
    assert "retain unknown outcomes" in detail.text
    assert "skill-usage/review-observation" in detail.text
    material_links = {
        unescape(value)
        for value in re.findall(r'href="([^"]+)"', detail.text)
        if urlsplit(unescape(value)).path.startswith("/dashboard/evidence/")
    }
    assert material_links
    for link in material_links:
        material = dashboard.get(link)
        assert material.status_code == 200
        assert "The report leaves unsupported outcomes unknown." in material.text
        assert material.url.params["origin"] == "skill"
    assert (
        "review-report"
        not in dashboard.get("/dashboard/methods", params={"scope": scope, "kind": "skill", "q": "unrelated"}).text
    )


def test_memory_exact_revision_and_cross_scope_isolation(dashboard: TestClient) -> None:
    first = create_scope(dashboard, "Library")
    other = create_scope(dashboard, "Unrelated")
    saved = dashboard.post(
        "/v1/memory/remember",
        json={
            "scope_id": first["scope_id"],
            "kind": "constraint",
            "text": "Keep <public> interfaces stable & preserve line breaks.\nReview every migration.",
        },
    )
    assert saved.status_code == 200
    artifact = saved.json()["records"][0]["artifact"]
    query = {
        "scope": first["scope_id"],
        "artifact": artifact["artifact_id"],
        "revision": artifact["revision"],
    }
    response = dashboard.get("/dashboard/notes", params=query)
    assert response.status_code == 200
    assert "&lt;public&gt;" in response.text
    assert "<public>" not in response.text
    assert dashboard.get("/dashboard/notes", params={**query, "revision": "999"}).status_code == 404
    assert (
        dashboard.get(
            "/dashboard/notes", params={key: value for key, value in query.items() if key != "revision"}
        ).status_code
        == 422
    )
    denied = dashboard.get(
        f"/v1/scopes/{other['scope_id']}/artifacts/atomic-memory/{artifact['artifact_id']}/revisions/{artifact['revision']}"
    )
    crossed = dashboard.get("/dashboard/notes", params={**query, "scope": other["scope_id"]})
    assert denied.is_error
    assert crossed.status_code == denied.status_code
    assert "Review every migration" not in crossed.text
    assert "Review every migration" not in dashboard.get("/dashboard/notes", params={"scope": other["scope_id"]}).text
    partial = dashboard.get("/dashboard/notes", params=query, headers={"HX-Request": "true"})
    assert "<!doctype" not in partial.text
    restored = dashboard.get(
        "/dashboard/notes", params=query, headers={"HX-Request": "true", "HX-History-Restore-Request": "true"}
    )
    assert "<!doctype" in restored.text
    assert restored.headers["cache-control"] == "no-store"


def test_reviewed_methods_link_to_exact_memory_evidence(dashboard: TestClient) -> None:
    scope = create_scope(dashboard, "Dream evidence")["scope_id"]
    saved = dashboard.post(
        "/v1/memory/remember",
        json={"scope_id": scope, "kind": "fact", "text": "The original retry preserved one committed record."},
    )
    assert saved.status_code == 200
    memory_ref = saved.json()["records"][0]["artifact"]
    artifact = None
    for family, proposal in (
        (
            "experience",
            {
                "situation": "A write response was lost.",
                "action": "Reuse the idempotency key.",
                "outcome": "One record remained.",
                "lesson": "Verify deduplication before retrying writes.",
            },
        ),
        (
            "skill",
            {
                "name": "retry-reviewed-writes",
                "description": "Use the reviewed retry contract.",
                "instructions": "Retain the original key and check the final record.",
                "validation": ["A replay preserves one record."],
            },
        ),
    ):
        lineage = {"artifact_refs": [memory_ref if artifact is None else artifact]}
        proposed = dashboard.post(
            f"/v1/{family}/propose",
            json={"scope_id": scope, "proposal": proposal, "source_refs": [], "artifact_refs": [], **lineage},
        )
        assert proposed.status_code == 201, proposed.text
        candidate = proposed.json()
        approved = dashboard.post(
            "/v1/candidates/approve",
            json={
                "scope_id": scope,
                "candidate_id": candidate["candidate_id"],
                "expected_version": candidate["version"],
            },
        )
        assert approved.status_code == 200, approved.text
        artifact = approved.json()["result_artifact"]
    memory_url = f"/v1/scopes/{scope}/artifacts/atomic-memory/{memory_ref['artifact_id']}"
    current = dashboard.get(memory_url)
    revised = dashboard.put(
        memory_url,
        headers={"If-Match": current.headers["etag"]},
        json={"content": {"kind": "fact", "text": "The retry contract was later refined."}},
    )
    assert revised.status_code == 200
    detail = dashboard.get(
        "/dashboard/skill",
        params={"scope": scope, "artifact": artifact["artifact_id"], "revision": artifact["revision"]},
    )
    assert detail.status_code == 200
    links = [unescape(value) for value in re.findall(r'href="([^"]+)"', detail.text)]
    experience_link = next(value for value in links if urlsplit(value).path == "/dashboard/experience")
    experience = dashboard.get(experience_link)
    assert experience.status_code == 200
    links = [unescape(value) for value in re.findall(r'href="([^"]+)"', experience.text)]
    memory_link = next(
        value
        for value in links
        if urlsplit(value).path == "/dashboard/notes"
        and parse_qs(urlsplit(value).query).get("artifact") == [memory_ref["artifact_id"]]
    )
    query = parse_qs(urlsplit(memory_link).query)
    assert query["scope"] == [scope]
    assert query["artifact"] == [memory_ref["artifact_id"]]
    assert query["revision"] == [str(memory_ref["revision"])]
    historical = dashboard.get(memory_link)
    assert historical.status_code == 200
    assert "The original retry preserved one committed record." in historical.text
    assert LABELS["historical_revision"] in historical.text
    searched_history = dashboard.get(
        memory_link, params={**{key: value[0] for key, value in query.items()}, "q": "refined", "mode": "fts"}
    )
    assert searched_history.status_code == 200
    assert 'data-memory-channel="text"' in searched_history.text
    selected_header = re.search(r'<button\b[^>]*aria-expanded="true"[^>]*>(.*?)</button>', searched_history.text, re.S)
    assert selected_header is not None
    assert "The original retry preserved" in selected_header[1]
    assert "memory-search-evidence" not in selected_header[1]
    other = create_scope(dashboard, "Unrelated Dream evidence")["scope_id"]
    denied_read = dashboard.get(
        f"/v1/scopes/{other}/artifacts/atomic-memory/{memory_ref['artifact_id']}/revisions/{memory_ref['revision']}"
    )
    crossed = dashboard.get(
        "/dashboard/notes", params={**{key: value[0] for key, value in query.items()}, "scope": other}
    )
    assert denied_read.is_error
    assert crossed.status_code == denied_read.status_code
    assert "The original retry preserved one committed record." not in crossed.text
    dashboard.headers.pop("Authorization")
    denied = dashboard.get(memory_link)
    assert denied.status_code == 401
    assert "The original retry preserved one committed record." not in denied.text


def test_authentication_recovers_without_exposing_credentials(tmp_path: Path) -> None:
    token = token_urlsafe(24)
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/auth.db"),
            dashboard=DashboardConfig(enabled=True),
            auth=BearerAuthConfig(enabled=True, token=SecretStr(token)),
            mcp=McpConfig(enabled=False),
        ),
    )
    with TestClient(app) as client:
        assert client.get("/dashboard/home").status_code == 401
        assert client.get("/dashboard/static/layout.css").status_code == 200
        assert (
            client.post(
                "/dashboard/session", data={"token": token}, headers={"Origin": "https://foreign.test"}
            ).status_code
            == 403
        )
        response = client.post("/dashboard/session", data={"token": token}, headers={"Origin": "http://testserver"})
        assert response.status_code == 200
        assert token not in response.text
        assert client.get("/v1/scopes").status_code == 401
        assert client.get("/v1/scopes", headers={"Authorization": f"Bearer {token}"}).status_code == 200
        assert (
            client.post(
                "/dashboard/session", data={"token": "invalid"}, headers={"Origin": "http://testserver"}
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/dashboard/session", data={"token": token}, headers={"Origin": "http://testserver"}
            ).status_code
            == 200
        )


def commit_handoff(dashboard: TestClient, scope: str) -> dict[str, Any]:
    source = dashboard.post(
        "/v1/sources/content",
        json={"scope_id": scope, "source_id": "review", "content": "Review remains in progress."},
    ).json()["source"]
    citation = {"kind": "source", "source_ref": source}
    prepared = dashboard.post(
        "/v1/handoff/finalize",
        json={
            "scope_id": scope,
            "draft": {
                "objective": "Review the HTTP contract",
                "state": [{"text": "Review remains in progress.", "citations": [citation]}],
                "disposition": "continuable",
                "next_action": {"text": "Continue the review.", "citations": [citation]},
                "omissions": [],
            },
        },
    )
    assert prepared.status_code == 200
    committed = dashboard.post("/v1/handoff/commit", json={"scope_id": scope, "handoff": prepared.json()})
    assert committed.status_code == 200
    return committed.json()["reference"]


def test_committed_handoff_json_and_its_sources_are_readable(dashboard: TestClient) -> None:
    scope = create_scope(dashboard, "Contract review")["scope_id"]
    record = commit_handoff(dashboard, scope)
    query = {"scope": scope, "artifact": record["artifact_id"], "revision": record["revision"]}
    response = dashboard.get("/dashboard/handoff-detail", params=query)
    assert response.status_code == 200
    assert "Review remains in progress." in response.text
    assert "Continue the review." in response.text
    evidence = dashboard.get(
        "/dashboard/evidence/review", params={**query, "origin": "handoff-detail", "source_type": "content"}
    )
    assert evidence.status_code == 200
    assert "Review remains in progress." in evidence.text
    assert (
        dashboard.get(
            "/dashboard/evidence/missing", params={**query, "origin": "handoff-detail", "source_type": "content"}
        ).status_code
        == 404
    )


def test_dashboard_is_opt_in_and_enforced_access_requires_the_static_token_profile(tmp_path: Path, monkeypatch) -> None:
    from powercontext.builtin.authz import PrincipalRef
    from powercontext.server.authentication import StaticBearerAuthenticationProvider

    monkeypatch.setenv("POWERCONTEXT_SERVER_DASHBOARD_ENABLED", "false")
    database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/disabled.db")
    settings = ServerSettings(database=database, mcp=McpConfig(enabled=False))
    with TestClient(create_server_app(settings=settings)) as client:
        assert client.get("/health/ready").status_code == 200
        assert client.get("/v1/scopes/default").status_code == 200
        for path in ("/", "/dashboard/home", "/dashboard/static/layout.css"):
            assert client.get(path).status_code == 404
        assert client.post("/dashboard/session", data={"token": "unused"}).status_code == 404

    monkeypatch.setenv("POWERCONTEXT_SERVER_DASHBOARD_ENABLED", "true")
    with pytest.raises(ValueError, match="AUTH_TOKEN"):
        ServerSettings(database=database, access=AccessControlConfig(mode="enforced"))
    token = token_urlsafe(24)
    settings = ServerSettings(
        database=database,
        access=AccessControlConfig(mode="enforced"),
        auth=BearerAuthConfig(token=SecretStr(token)),
        mcp=McpConfig(enabled=False),
    )
    with pytest.raises(ValueError, match="built-in static Bearer profile"):
        create_server_app(
            settings=settings,
            authentication_provider=StaticBearerAuthenticationProvider(token, PrincipalRef(type="user", id="member")),
        )


def test_disabled_dashboard_does_not_offer_token_login(tmp_path: Path) -> None:
    token = token_urlsafe(24)
    settings = ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/disabled-auth.db"),
        dashboard=DashboardConfig(enabled=False),
        access=AccessControlConfig(mode="enforced"),
        auth=BearerAuthConfig(token=SecretStr(token)),
        mcp=McpConfig(enabled=False),
    )
    with TestClient(create_server_app(settings=settings)) as client:
        response = client.get("/dashboard/home")
        assert response.status_code == 401
        assert response.headers["content-type"].startswith("application/json")
        assert client.get("/dashboard/home", headers={"Authorization": f"Bearer {token}"}).status_code == 404


@pytest.mark.parametrize("broken_family", [None, "atomic-memory", "handoff", "experience", "skill"])
def test_home_previews_are_independent_of_missing_or_corrupt_notes(
    dashboard: TestClient, broken_family: str | None
) -> None:
    from sqlalchemy import update

    from powercontext.builtin.persistence.tables import ARTIFACTS_TABLE

    scope = create_scope(dashboard, "Independent content sections")["scope_id"]
    if broken_family is not None:
        created = dashboard.post(
            f"/v1/scopes/{scope}/artifacts",
            json={"family": "atomic-memory", "content": {"kind": "fact", "text": "Healthy atomic preview."}},
        )
        assert created.status_code == 201
    commit_handoff(dashboard, scope)
    previews = {
        "experience": {
            "situation": "Independent preview",
            "action": "Read the report",
            "outcome": "Unknown",
            "lesson": "Healthy experience preview.",
        },
        "skill": {
            "name": "healthy-skill-preview",
            "description": "Read the report",
            "instructions": "Preserve its limits.",
            "validation": ["Keep unknown outcomes."],
        },
    }
    for family, proposal in previews.items():
        proposed = dashboard.post(
            f"/v1/{family}/propose",
            json={
                "scope_id": scope,
                "proposal": proposal,
                "source_refs": [{"name": "content", "source_id": "review"}],
                "artifact_refs": [],
            },
        )
        assert proposed.status_code == 201, proposed.text
        candidate = proposed.json()
        approved = dashboard.post(
            "/v1/candidates/approve",
            json={
                "scope_id": scope,
                "candidate_id": candidate["candidate_id"],
                "expected_version": candidate["version"],
            },
        )
        assert approved.status_code == 200, approved.text

    async def corrupt_body() -> None:
        async with cast(FastAPI, dashboard.app).state.application.primary_database.transaction() as connection:
            await connection.execute(
                update(ARTIFACTS_TABLE)
                .where(ARTIFACTS_TABLE.c.scope_id == scope, ARTIFACTS_TABLE.c.family == broken_family)
                .values(content=b"invalid stored body")
            )

    assert dashboard.portal is not None
    if broken_family is not None:
        dashboard.portal.call(corrupt_body)
    response = dashboard.get("/dashboard/home", params={"scope": scope})
    assert response.status_code == 200
    assert (LABELS["error_503"] in response.text) is (broken_family is not None)
    assert ("Healthy atomic preview." in response.text) is (broken_family not in {None, "atomic-memory"})
    assert ("Review the HTTP contract" in response.text) is (broken_family != "handoff")
    assert ("Healthy experience preview." in response.text) is (broken_family != "experience")
    assert ("healthy-skill-preview" in response.text) is (broken_family != "skill")
