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
from html import unescape
from pathlib import Path
from secrets import token_urlsafe
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from powercontext.builtin.persistence.sqlite import SQLiteConfig
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
        scheduler_path=tmp_path / "scheduler.db",
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


def test_prompt_dashboard_opens_without_profile_page(dashboard: TestClient) -> None:
    prompts = dashboard.get("/dashboard/prompts", params={"lang": "en"})
    profile = dashboard.get("/dashboard/profile", params={"lang": "en"})
    assert prompts.status_code == 200
    assert "Prompts" in prompts.text
    assert profile.status_code == 404
    assert "Profile" not in prompts.text


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


def test_legacy_bookmark_preserves_scope_on_redirect(dashboard: TestClient) -> None:
    response = dashboard.get("/dashboard/guide?scope=chosen&period=30d", follow_redirects=False)
    assert response.is_redirect
    assert response.headers["location"] == "/dashboard/home?scope=chosen&period=30d"


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


def test_memory_pagination_and_deep_link_select_the_corresponding_text(dashboard: TestClient) -> None:
    scope = create_scope(dashboard, "Long memory")["scope_id"]
    for index in range(25):
        response = dashboard.post(
            "/v1/memory/remember",
            json={"scope_id": scope, "kind": "constraint", "text": f"Recorded constraint {index}."},
        )
        assert response.status_code == 200
    entries = dashboard.post("/v1/memory/entries/list", json={"scope_id": scope}).json()["entries"]
    first = dashboard.get("/dashboard/notes", params={"scope": scope})
    expected = {item["citation"]["entry_id"] for item in entries}
    assert collect_pages(dashboard, first.text, "/dashboard/notes", "entry") == expected
    entry = entries[-1]["citation"]
    deep = dashboard.get("/dashboard/notes", params={"scope": scope, "entry": entry["entry_id"]})
    assert deep.status_code == 200
    assert entry["entry_id"] in record_links(deep.text, "/dashboard/notes", "entry")
    assert entries[-1]["text"] in deep.text
    assert dashboard.get("/dashboard/notes", params={"scope": scope, "notes_page": "invalid"}).status_code == 422
    assert dashboard.get("/dashboard/notes", params={"scope": scope, "notes_page": "99"}).status_code == 404


def test_memory_search_preserves_scope_citations_and_result_pagination(dashboard: TestClient) -> None:
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
        "/v1/memory/search", json={"scope_id": scope, "query": "Release", "mode": "fts", "limit": 50}
    ).json()["hits"]
    assert hits
    first = dashboard.get("/dashboard/notes", params={"scope": scope, "q": "Release"})
    assert first.status_code == 200
    assert LABELS["constraint"] in first.text
    assert LABELS["page_number"].format(page=1) in first.text
    second = dashboard.get(page_link(first.text, LABELS["next_page"]))
    assert second.status_code == 200
    assert second.url.params["q"] == "Release"
    assert second.url.params["scope"] == scope
    assert LABELS["page_number"].format(page=2) in second.text
    previous = dashboard.get(page_link(second.text, LABELS["previous_page"]))
    assert record_links(previous.text, "/dashboard/notes", "entry") == record_links(
        first.text, "/dashboard/notes", "entry"
    )
    assert collect_pages(dashboard, first.text, "/dashboard/notes", "entry") == {
        hit["citation"]["entry_id"] for hit in hits
    }
    for hit in hits:
        citation = hit["citation"]
        selected = dashboard.get(
            "/dashboard/notes",
            params={
                "scope": scope,
                "q": "Release",
                "entry": citation["entry_id"],
                "entry_version": citation["entry_version_id"],
                "memory_id": citation["memory_ref"]["artifact_id"],
                "memory_revision": citation["memory_ref"]["revision"],
            },
        )
        assert selected.status_code == 200
        assert hit["text"] in selected.text
    for target, query in [(other, "Release"), (scope, "nonexistent")]:
        empty = dashboard.get("/dashboard/notes", params={"scope": target, "q": query})
        assert empty.status_code == 200
        assert LABELS["notes_no_match"] in empty.text
        assert LABELS["page_number"].format(page=1) in empty.text
        assert not record_links(empty.text, "/dashboard/notes", "entry")
    single = dashboard.get("/dashboard/notes", params={"scope": scope, "q": "Invoices"})
    assert LABELS["page_number"].format(page=1) in single.text
    assert LABELS["previous_page"] in single.text
    assert LABELS["next_page"] in single.text
    restored = dashboard.get(page_link(first.text, LABELS["clear_search"]))
    assert restored.status_code == 200
    assert not restored.url.params.get("q")
    expected = dashboard.post("/v1/memory/entries/list", json={"scope_id": scope}).json()["entries"]
    assert collect_pages(dashboard, restored.text, "/dashboard/notes", "entry") == {
        item["citation"]["entry_id"] for item in expected
    }


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
            "/v1/artifact-candidates/approve",
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
        "/v1/artifact-candidates/approve",
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
        "/v1/artifact-candidates/approve",
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
    entry = saved.json()["entry"]
    citation = entry["citation"]
    query = {
        "scope": first["scope_id"],
        "entry": citation["entry_id"],
        "memory_id": citation["memory_ref"]["artifact_id"],
        "memory_revision": citation["memory_ref"]["revision"],
        "entry_version": citation["entry_version_id"],
    }
    response = dashboard.get("/dashboard/notes", params=query)
    assert response.status_code == 200
    assert "&lt;public&gt;" in response.text
    assert "<public>" not in response.text
    assert dashboard.get("/dashboard/notes", params={**query, "entry_version": "missing"}).status_code == 404
    assert (
        dashboard.get(
            "/dashboard/notes", params={key: value for key, value in query.items() if key != "memory_id"}
        ).status_code
        == 422
    )
    denied = dashboard.post("/v1/memory/entries/get", json={"scope_id": other["scope_id"], "citation": citation})
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
    citation = saved.json()["entry"]["citation"]
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
        lineage = {"memory_citations": [citation]} if artifact is None else {"artifact_refs": [artifact]}
        proposed = dashboard.post(
            f"/v1/{family}/propose",
            json={"scope_id": scope, "proposal": proposal, "source_refs": [], "artifact_refs": [], **lineage},
        )
        assert proposed.status_code == 201, proposed.text
        candidate = proposed.json()
        approved = dashboard.post(
            "/v1/artifact-candidates/approve",
            json={
                "scope_id": scope,
                "candidate_id": candidate["candidate_id"],
                "expected_version": candidate["version"],
            },
        )
        assert approved.status_code == 200, approved.text
        artifact = approved.json()["result_artifact"]
    revised = dashboard.post(
        "/v1/memory/entries/revise",
        json={"scope_id": scope, "citation": citation, "kind": "fact", "text": "The retry contract was later refined."},
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
    memory_link = next(value for value in links if "entry_version=" in value)
    query = parse_qs(urlsplit(memory_link).query)
    assert query["scope"] == [scope]
    assert query["entry"] == [citation["entry_id"]]
    assert query["entry_version"] == [citation["entry_version_id"]]
    assert query["memory_id"] == [citation["memory_ref"]["artifact_id"]]
    assert query["memory_revision"] == [str(citation["memory_ref"]["revision"])]
    historical = dashboard.get(memory_link)
    assert historical.status_code == 200
    assert "The original retry preserved one committed record." in historical.text
    other = create_scope(dashboard, "Unrelated Dream evidence")["scope_id"]
    denied_read = dashboard.post("/v1/memory/entries/get", json={"scope_id": other, "citation": citation})
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
        scheduler_path=tmp_path / "scheduler.db",
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


def test_dashboard_is_opt_in_and_requires_the_static_token_profile(tmp_path: Path, monkeypatch) -> None:
    from powercontext.server.authentication import StaticBearerAuthenticationProvider
    from powercontext.server.authz import PrincipalRef

    monkeypatch.setenv("POWERCONTEXT_SERVER_DASHBOARD_ENABLED", "false")
    database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/disabled.db")
    settings = ServerSettings(database=database, mcp=McpConfig(enabled=False))
    with TestClient(create_server_app(settings=settings, scheduler_path=tmp_path / "scheduler.db")) as client:
        assert client.get("/health/ready").status_code == 200
        assert client.get("/v1/scopes/default").status_code == 200
        for path in ("/", "/dashboard/home", "/dashboard/static/layout.css"):
            assert client.get(path).status_code == 404
        assert client.post("/dashboard/session", data={"token": "unused"}).status_code == 404

    monkeypatch.setenv("POWERCONTEXT_SERVER_DASHBOARD_ENABLED", "true")
    with pytest.raises(ValueError, match="DASHBOARD_ENABLED requires"):
        ServerSettings(database=database)
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
    with TestClient(create_server_app(settings=settings, scheduler_path=tmp_path / "scheduler.db")) as client:
        response = client.get("/dashboard/home")
        assert response.status_code == 401
        assert response.headers["content-type"].startswith("application/json")
        assert client.get("/dashboard/home", headers={"Authorization": f"Bearer {token}"}).status_code == 404
