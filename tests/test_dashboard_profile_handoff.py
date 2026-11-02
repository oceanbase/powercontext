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

"""Read and download exact artifacts through the authenticated Dashboard."""

import json
import re
import sqlite3
from html import unescape
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from alembic import command
from fastapi.testclient import TestClient
from markdown_it import MarkdownIt
from sqlalchemy import create_engine

from powercontext.builtin.artifacts.prompt import PROMPT_KEYS, PromptRegistry
from powercontext.builtin.artifacts.prompt.builtin import builtin_prompt_definitions
from powercontext.builtin.persistence.migrations.deployment import deployment_runner, server_bundle
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.runtime.processing_registry import canonical_processing_manifest
from powercontext.server.dashboard.api import DashboardAPI
from powercontext.server.dashboard.pagination import PAGE_SIZE
from powercontext.server.factory import create_server_app
from powercontext.server.settings import (
    DashboardConfig,
    McpConfig,
    ServerSettings,
)
from tests.test_dashboard import commit_handoff, create_scope
from tests.test_dashboard import dashboard as dashboard


def _links(html: str, path: str) -> list[str]:
    return [unescape(url) for url in re.findall(r'href="([^"]+)"', html) if urlsplit(unescape(url)).path == path]


def _profile(client: TestClient, scope: str, body: str) -> dict[str, Any]:
    response = client.post(f"/v1/scopes/{scope}/artifacts", json={"family": "profile", "content": {"content": body}})
    assert response.status_code == 201, response.text
    return response.json()


def test_profile_history_is_exact_and_returns_to_its_page(dashboard):
    scope = create_scope(dashboard, "Profile history")["scope_id"]
    _profile(dashboard, scope, "# First profile\n\nOriginal preference.")
    path = f"/v1/scopes/{scope}/artifacts/profile/profile"
    for revision in range(2, 9):
        previous = dashboard.get(path)
        changed = dashboard.put(
            path,
            json={"content": {"content": f"# Profile {revision}\n\nUpdated preference."}},
            headers={"If-Match": previous.headers["etag"]},
        )
        assert changed.status_code == 200, changed.text
    current = dashboard.get("/dashboard/profile", params={"scope": scope, "lang": "en"})
    assert current.status_code == 200
    assert "<h1>Profile 8</h1>" in current.text
    first = dashboard.get("/dashboard/profile", params={"scope": scope, "view": "history", "lang": "en"})
    next_url = next(url for url in _links(first.text, "/dashboard/profile") if "profile_cursor=" in url)
    second = dashboard.get(next_url)
    detail_url = next(
        url
        for url in _links(second.text, "/dashboard/profile")
        if parse_qs(urlsplit(url).query).get("revision") == ["1"]
    )
    detail = dashboard.get(detail_url)
    assert detail.status_code == 200
    assert "Historical profile" in detail.text
    assert "<h1>First profile</h1>" in detail.text
    assert "Updated preference" not in detail.text
    target = parse_qs(urlsplit(detail_url).query)["return_to"][0]
    assert target in _links(detail.text, "/dashboard/profile")
    assert dashboard.get(target).status_code == 200
    # Reloads preserve the URL's history position and never mutate the head.
    assert dashboard.get(detail_url).status_code == 200
    assert dashboard.get(path).json()["revision"] == 8


def test_profile_safe_markdown_and_scope_isolation(dashboard):
    scope = create_scope(dashboard, "Safe profile")["scope_id"]
    other = create_scope(dashboard, "Empty profile")["scope_id"]
    _profile(
        dashboard,
        scope,
        "# Chinese 中文\n\n<script>alert(1)</script>\n\n![tracking](https://example.com/pixel)\n\n[unsafe](javascript:alert(1))\n\n[safe](https://example.com)",
    )
    page = dashboard.get("/dashboard/profile", params={"scope": scope})
    assert page.status_code == 200
    assert "Chinese 中文" in page.text
    assert "<script>alert(1)</script>" not in page.text
    assert 'src="https://example.com/pixel"' not in page.text
    assert 'href="javascript:' not in page.text
    assert 'href="https://example.com" rel="noopener noreferrer" target="_blank"' in page.text
    empty = dashboard.get("/dashboard/profile", params={"scope": other, "lang": "en"})
    assert "No profile in this scope" in empty.text
    assert "Chinese 中文" not in empty.text
    for params in ({"revision": "0"}, {"revision": "no"}, {"view": "draft"}):
        assert dashboard.get("/dashboard/profile", params={"scope": scope, **params}).status_code == 422
    combined = dashboard.get("/dashboard/profile", params={"scope": scope, "view": "history", "revision": "1"})
    assert combined.status_code == 200
    assert "Chinese 中文" in combined.text


def test_profile_page_rolls_back_to_a_new_revision(dashboard):
    scope = create_scope(dashboard, "Profile rollback")["scope_id"]
    original = "# First profile\n\nOriginal preference."
    _profile(dashboard, scope, original)
    path = f"/v1/scopes/{scope}/artifacts/profile/profile"
    current = dashboard.get(path)
    changed = dashboard.put(
        path,
        json={"content": {"content": "# Second profile\n\nLater preference."}},
        headers={"If-Match": current.headers["etag"]},
    )
    assert changed.status_code == 200, changed.text
    opened = dashboard.get("/dashboard/profile", params={"scope": scope, "revision": "1", "history": "1", "lang": "en"})
    assert opened.status_code == 200
    assert 'name="reason"' in opened.text
    stale = dashboard.post(
        "/dashboard/rollback",
        data={
            "scope": scope,
            "family": "profile",
            "artifact_id": "profile",
            "source_revision": "1",
            "expected_revision": "1",
            "reason": "Restore the approved profile",
        },
        headers={"Origin": "http://testserver"},
    )
    assert stale.status_code == 200
    assert "The current revision changed" in stale.text
    restored = dashboard.post(
        "/dashboard/rollback",
        data={
            "scope": scope,
            "family": "profile",
            "artifact_id": "profile",
            "source_revision": "1",
            "expected_revision": "2",
            "reason": "Restore the approved profile",
        },
        headers={"Origin": "http://testserver"},
    )
    assert restored.status_code == 200
    assert "Confirm rollback" not in restored.text
    head = dashboard.get(path)
    assert head.status_code == 200
    body = head.json()
    assert body["revision"] == 3
    stored = dashboard.get(f"{path}/revisions/1").json()
    assert body["content"]["content"] == stored["content"]["content"]
    assert body["restored_from_revision"] == 1
    assert body["reason"] == "Restore the approved profile"
    assert body["created_by"]["id"]


def test_rollback_is_offered_when_access_control_is_disabled(tmp_path: Path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/open.db"),
            dashboard=DashboardConfig(enabled=True),
            mcp=McpConfig(enabled=False),
        ),
        scheduler_path=tmp_path / "scheduler.db",
    )
    with TestClient(app) as client:
        scope = create_scope(client, "Open rollback")["scope_id"]
        _profile(client, scope, "# First profile\n\nOriginal preference.")
        current = client.get(f"/v1/scopes/{scope}/artifacts/profile/profile")
        changed = client.put(
            f"/v1/scopes/{scope}/artifacts/profile/profile",
            json={"content": {"content": "# Second profile\n\nLater preference."}},
            headers={"If-Match": current.headers["etag"]},
        )
        assert changed.status_code == 200, changed.text
        opened = client.get(
            "/dashboard/profile", params={"scope": scope, "revision": "1", "history": "1", "lang": "en"}
        )
        assert opened.status_code == 200
        assert 'name="reason"' in opened.text
        assert 'type="number"' in opened.text


def test_existing_database_without_revision_metadata_columns_keeps_working(tmp_path: Path) -> None:
    settings = ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/existing.db"),
        dashboard=DashboardConfig(enabled=True),
        mcp=McpConfig(enabled=False),
    )

    def open_app():
        return create_server_app(
            settings=settings,
            scheduler_path=tmp_path / "scheduler.db",
        )

    # Use the registered release layout, then migrate before normal admission.
    scope = "existing"
    content = json.dumps({
        "schema": "powercontext.profile.v1",
        "media_type": "text/markdown",
        "content": "# First profile\n\nOriginal preference.\n",
        "generation": {"mode": "manual_create", "created_at": "2026-01-01T00:00:00Z"},
    }).encode()
    engine = create_engine(f"sqlite:///{tmp_path}/existing.db")
    bundle = server_bundle()
    try:
        with engine.begin() as connection:
            config = bundle.config(connection)
            config.attributes["configuration"] = {"processing_manifest": canonical_processing_manifest(BuiltinConfig())}
            command.upgrade(config, "s1300")
            connection.exec_driver_sql("DROP TABLE pc_schema_revision")
            connection.exec_driver_sql(
                "INSERT INTO pc_scopes (scope_id,title,summary,scope_id_search,title_search,summary_search,version) "
                "VALUES (?, 'Existing database', '', ?, 'existing database', '', 1)",
                (scope, scope),
            )
            connection.exec_driver_sql(
                "INSERT INTO pc_artifacts (scope_id,family,artifact_id,revision,content) VALUES (?, 'profile', 'profile', 1, ?)",
                (scope, content),
            )
            connection.exec_driver_sql(
                "INSERT INTO pc_artifact_heads (scope_id,family,artifact_id,revision) VALUES (?, 'profile', 'profile', 1)",
                (scope,),
            )
            columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(pc_artifacts)")}
            assert not columns & {"created_at", "created_by", "restored_from_revision", "rollback_reason"}
    finally:
        engine.dispose()
    runner = deployment_runner(settings)
    plan = runner.plan()
    assert plan.source_revision == "s1300"
    assert runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True).state == "ready"
    with TestClient(open_app()) as client:
        path = f"/v1/scopes/{scope}/artifacts/profile/profile"
        current = client.get(path)
        assert current.status_code == 200, current.text
        assert current.json()["content"]["content"] == "# First profile\n\nOriginal preference.\n"
        assert current.json()["created_at"] is None
        changed = client.put(
            path,
            json={"content": {"content": "# Second profile\n\nLater preference."}},
            headers={"If-Match": current.headers["etag"]},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["created_at"] is not None


def test_profile_compare_accepts_a_revision_outside_the_current_page(dashboard: TestClient) -> None:
    scope = create_scope(dashboard, "Profile compare")["scope_id"]
    _profile(dashboard, scope, "# First profile\n\nOriginal preference.")
    path = f"/v1/scopes/{scope}/artifacts/profile/profile"
    for revision in range(2, 9):
        previous = dashboard.get(path)
        changed = dashboard.put(
            path,
            json={"content": {"content": f"# Profile {revision}\n\nUpdated preference."}},
            headers={"If-Match": previous.headers["etag"]},
        )
        assert changed.status_code == 200, changed.text
    compared = dashboard.get(
        "/dashboard/profile",
        params={"scope": scope, "revision": "1", "compare": "8", "lang": "en"},
    )
    assert compared.status_code == 200
    assert 'id="compare-revision"' in compared.text
    assert 'type="number"' in compared.text
    assert "First profile" in compared.text
    assert "Profile 8" in compared.text
    missing = dashboard.get(
        "/dashboard/profile",
        params={"scope": scope, "revision": "1", "compare": "99", "lang": "en"},
    )
    assert "That revision could not be compared." in missing.text


def test_prompt_page_shows_the_selected_revision(dashboard: TestClient) -> None:
    # This fixture has no generation model, so custom prompts are otherwise rejected.
    writer = cast(Any, dashboard.app).state.application._records()._family_writers.get("prompt")
    writer._registry = PromptRegistry(builtin_prompt_definitions(), supported=frozenset(PROMPT_KEYS))
    scope = create_scope(dashboard, "Prompt history")["scope_id"]
    created = dashboard.post(
        f"/v1/scopes/{scope}/artifacts",
        json={
            "family": "prompt",
            "prompt_key": "experience.generate",
            "content": {
                "schema_version": "powercontext.prompt.v1",
                "mode": "custom",
                "instructions": "Keep the approved prompt.",
                "demonstrations": [],
            },
        },
    )
    assert created.status_code == 201, created.text
    current = dashboard.get(f"/v1/scopes/{scope}/artifacts/prompt/experience.generate")
    replaced = dashboard.put(
        f"/v1/scopes/{scope}/artifacts/prompt/experience.generate",
        json={
            "content": {
                "schema_version": "powercontext.prompt.v1",
                "mode": "custom",
                "instructions": "Keep a later prompt.",
                "demonstrations": [],
            }
        },
        headers={"If-Match": current.headers["etag"]},
    )
    assert replaced.status_code == 200, replaced.text
    opened = dashboard.get(
        "/dashboard/prompts",
        params={"scope": scope, "prompt_key": "experience.generate", "prompt_revision": "1", "lang": "en"},
    )
    assert opened.status_code == 200
    assert "Keep the approved prompt." in opened.text
    assert "Keep a later prompt." not in opened.text
    missing = dashboard.get(
        "/dashboard/prompts",
        params={"scope": scope, "prompt_key": "experience.generate", "prompt_revision": "99", "lang": "en"},
    )
    assert missing.status_code == 200
    assert 'role="alert"' in missing.text
    assert "Keep a later prompt." not in missing.text
    assert "/dashboard/rollback" not in missing.text
    assert 'id="compare-revision"' not in missing.text


@pytest.mark.parametrize(("historical_mode", "current_mode"), [("custom", "auto"), ("auto", "custom")])
def test_prompt_mode_matches_the_opened_revision(
    dashboard: TestClient, historical_mode: str, current_mode: str
) -> None:
    writer = cast(Any, dashboard.app).state.application._records()._family_writers.get("prompt")
    writer._registry = PromptRegistry(builtin_prompt_definitions(), supported=frozenset(PROMPT_KEYS))
    scope = create_scope(dashboard, "Prompt mode history")["scope_id"]
    key = "experience.generate"
    content = {
        "schema_version": "powercontext.prompt.v1",
        "mode": historical_mode,
        "instructions": "Keep the approved prompt." if historical_mode == "custom" else "",
        "demonstrations": [],
    }
    created = dashboard.post(
        f"/v1/scopes/{scope}/artifacts", json={"family": "prompt", "prompt_key": key, "content": content}
    )
    assert created.status_code == 201, created.text
    _put_content(
        dashboard,
        scope,
        "prompt",
        key,
        {
            **content,
            "mode": current_mode,
            "instructions": "Use the current prompt." if current_mode == "custom" else "",
        },
    )
    opened = dashboard.get(
        "/dashboard/prompts",
        params={"scope": scope, "prompt_key": key, "prompt_revision": "1", "lang": "en"},
    )
    assert opened.status_code == 200, opened.text
    historical_card = next(card for card in opened.text.split("</article>") if f">{key}</code>" in card)
    assert f"Mode: {historical_mode.title()}" in historical_card
    assert f"Mode: {current_mode.title()}" not in historical_card
    current = dashboard.get("/dashboard/prompts", params={"scope": scope, "lang": "en"})
    assert current.status_code == 200, current.text
    current_card = next(card for card in current.text.split("</article>") if f">{key}</code>" in card)
    assert f"Mode: {current_mode.title()}" in current_card


def _put_content(
    client: TestClient, scope: str, family: str, artifact_id: str, content: dict[str, Any]
) -> dict[str, Any]:
    path = f"/v1/scopes/{scope}/artifacts/{family}/{artifact_id}"
    current = client.get(path)
    assert current.status_code == 200, (family, current.text)
    replaced = client.put(path, json={"content": content}, headers={"If-Match": current.headers["etag"]})
    assert replaced.status_code == 200, (family, replaced.text)
    return replaced.json()


def _rollback(client: TestClient, scope: str, family: str, artifact_id: str, expected: int) -> Any:
    return client.post(
        "/dashboard/rollback",
        data={
            "scope": scope,
            "family": family,
            "artifact_id": artifact_id,
            "source_revision": "1",
            "expected_revision": str(expected),
            "reason": "Restore the approved revision",
        },
        headers={"Origin": "http://testserver"},
    )


def _hrefs(html: str) -> list[str]:
    return [unescape(href) for href in re.findall(r'href="([^"]+)"', html)]


def _revised_memory(client: TestClient, scope: str) -> str:
    remembered = client.post("/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": "Original note."})
    assert remembered.status_code == 200, remembered.text
    artifact_id = remembered.json()["records"][0]["artifact"]["artifact_id"]
    path = f"/v1/scopes/{scope}/artifacts/atomic-memory/{artifact_id}"
    current = client.get(path)
    revised = client.put(
        path, headers={"If-Match": current.headers["etag"]}, json={"content": {"kind": "fact", "text": "Revised note."}}
    )
    assert revised.status_code == 200, revised.text
    return artifact_id


def _reading(html: str) -> str:
    return html.split('id="note-reading"', 1)[1]


def test_atomic_memory_history_compares_its_own_revisions_read_only(dashboard: TestClient) -> None:
    scope = create_scope(dashboard, "Atomic memory history")["scope_id"]
    artifact_id = _revised_memory(dashboard, scope)
    page = dashboard.get(
        "/dashboard/notes",
        params={"scope": scope, "artifact": artifact_id, "revision": "1", "compare": "2", "lang": "en"},
    )
    assert page.status_code == 200, page.text
    assert "Original note." in _reading(page.text).split("Revision history", 1)[0]
    assert "Revision history" in page.text
    assert "+ Revised note." in unescape(page.text)
    assert "- Original note." in unescape(page.text)
    assert "/dashboard/rollback" not in page.text


def test_atomic_memory_history_link_opens_that_exact_revision(dashboard: TestClient) -> None:
    scope = create_scope(dashboard, "Atomic memory navigation")["scope_id"]
    artifact_id = _revised_memory(dashboard, scope)
    current = dashboard.get("/dashboard/notes", params={"scope": scope, "lang": "en"})
    assert current.status_code == 200, current.text
    assert "Revised note." in _reading(current.text)
    assert "Revision history" in current.text
    older = next(
        href
        for href in _hrefs(current.text)
        if f"artifact={artifact_id}" in href and "revision=1" in href.split("#")[0]
    )
    page = dashboard.get(older)
    assert page.status_code == 200, page.text
    reading = _reading(page.text).split("Revision history", 1)[0]
    assert "Original note." in reading
    assert "Revised note." not in reading


def test_notes_directory_link_drops_the_previous_notes_revision_cursor(dashboard: TestClient) -> None:
    scope = create_scope(dashboard, "Atomic memory cursor")["scope_id"]
    for index in range(PAGE_SIZE + 1):
        remembered = dashboard.post(
            "/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": f"Note {index}."}
        )
        assert remembered.status_code == 200, remembered.text
    first = dashboard.get("/dashboard/notes", params={"scope": scope, "lang": "en"})
    selected = re.search(r'atomic-memory/([^@"]+)@', first.text)
    assert selected is not None
    path = f"/v1/scopes/{scope}/artifacts/atomic-memory/{selected.group(1)}"
    for revision in range(2, PAGE_SIZE + 2):
        current = dashboard.get(path)
        revised = dashboard.put(
            path,
            headers={"If-Match": current.headers["etag"]},
            json={"content": {"kind": "fact", "text": f"Revised {revision}."}},
        )
        assert revised.status_code == 200, revised.text
    opened = dashboard.get("/dashboard/notes", params={"scope": scope, "history": "1", "lang": "en"})
    paged = dashboard.get(next(href for href in _hrefs(opened.text) if "notes_revision_cursor=" in href))
    assert paged.status_code == 200, paged.text
    directory = next(href for href in _hrefs(paged.text) if "notes_cursor=" in href)
    assert "notes_revision_cursor" not in directory
    following = dashboard.get(directory)
    assert following.status_code == 200, following.text
    assert "Revision history" in following.text


def test_atomic_memory_replace_rejects_rollback_fields(dashboard: TestClient) -> None:
    scope = create_scope(dashboard, "Atomic memory rollback")["scope_id"]
    artifact_id = _revised_memory(dashboard, scope)
    path = f"/v1/scopes/{scope}/artifacts/atomic-memory/{artifact_id}"
    current = dashboard.get(path)
    restored = dashboard.put(
        path,
        headers={"If-Match": current.headers["etag"]},
        json={"content": {"kind": "fact", "text": "Original note."}, "restored_from_revision": 1, "reason": "Undo"},
    )
    assert restored.status_code == 422, restored.text
    assert dashboard.get(path).json()["revision"] == 2


def test_rollback_rejects_a_cross_origin_request(dashboard: TestClient) -> None:
    scope = create_scope(dashboard, "Rollback origin")["scope_id"]
    _profile(dashboard, scope, "# First profile\n\nOriginal preference.")
    path = f"/v1/scopes/{scope}/artifacts/profile/profile"
    current = dashboard.get(path)
    changed = dashboard.put(
        path,
        json={"content": {"content": "# Second profile\n\nLater preference."}},
        headers={"If-Match": current.headers["etag"]},
    )
    assert changed.status_code == 200, changed.text
    payload = {
        "scope": scope,
        "family": "profile",
        "artifact_id": "profile",
        "source_revision": "1",
        "expected_revision": "2",
        "reason": "Restore the approved profile",
    }
    missing = dashboard.post("/dashboard/rollback", data=payload)
    foreign = dashboard.post("/dashboard/rollback", data=payload, headers={"Origin": "http://sibling.test"})
    assert missing.status_code == 403
    assert foreign.status_code == 403
    assert dashboard.get(path).json()["revision"] == 2


def test_topic_history_cursor_does_not_follow_a_link_to_another_topic(dashboard: TestClient) -> None:
    scope = create_scope(dashboard, "Topic history cursor")["scope_id"]
    ids = []
    for title in ("Topic A", "Topic B"):
        created = dashboard.post(
            f"/v1/scopes/{scope}/artifacts",
            json={"family": "topic-memory", "content": {"title": title, "summary": "Summary", "detail": "Detail."}},
        )
        assert created.status_code == 201, created.text
        ids.append(created.json()["artifact_id"])
    for revision in range(2, 10):
        _put_content(
            dashboard,
            scope,
            "topic-memory",
            ids[0],
            {"title": "Topic A", "summary": f"Summary {revision}", "detail": "Detail."},
        )
    opened = dashboard.get(
        "/dashboard/topics",
        params={"scope": scope, "topic_artifact": ids[0], "topic_revision": "9", "history": "1", "lang": "en"},
    )
    assert opened.status_code == 200, opened.text
    paged = dashboard.get(next(href for href in _hrefs(opened.text) if "topic_revision_cursor=" in href))
    assert paged.status_code == 200, paged.text
    other = next(href for href in _hrefs(paged.text) if f"topic_artifact={ids[1]}" in href)
    assert "topic_revision_cursor" not in other
    assert "Revision history" in dashboard.get(other).text


def test_dashboard_restores_a_legacy_instruction_only_skill_revision(dashboard: TestClient, tmp_path: Path) -> None:
    scope = create_scope(dashboard, "Legacy skill rollback")["scope_id"]
    legacy = {
        "name": "compatibility-check",
        "description": "Check compatibility before release",
        "instructions": "Run the approved compatibility tests.",
        "validation": ["Compatibility tests pass"],
    }
    created = dashboard.post(f"/v1/scopes/{scope}/artifacts", json={"family": "skill", "content": legacy})
    assert created.status_code == 201, created.text
    skill_id = created.json()["artifact_id"]
    _put_content(dashboard, scope, "skill", skill_id, {**legacy, "instructions": "Run a later set of tests."})
    # Revisions written before package-backed Skills stored the instruction core without a package.
    with sqlite3.connect(tmp_path / "data.db") as connection:
        connection.execute(
            "UPDATE pc_artifacts SET content = ? WHERE scope_id = ? AND family = 'skill' AND artifact_id = ? "
            "AND revision = 1",
            (json.dumps(legacy).encode(), scope, skill_id),
        )
    changed = dashboard.get(
        "/dashboard/skill",
        params={"scope": scope, "artifact": skill_id, "revision": "1", "compare": "2", "lang": "en"},
    )
    assert changed.status_code == 200, changed.text
    assert "- Run the approved compatibility tests." in unescape(changed.text)
    assert "+ Run a later set of tests." in unescape(changed.text)
    assert 'action="/dashboard/rollback"' in changed.text
    assert "The content matches the current revision, so rollback is unavailable." in changed.text
    restored = _rollback(dashboard, scope, "skill", skill_id, 2)
    assert restored.status_code == 200, restored.text
    head = dashboard.get(f"/v1/scopes/{scope}/artifacts/skill/{skill_id}").json()
    assert head["revision"] == 3
    assert head["content"]["instructions"].startswith("Run the approved compatibility tests.")
    equivalent = dashboard.get(
        "/dashboard/skill",
        params={"scope": scope, "artifact": skill_id, "revision": "1", "compare": "3", "lang": "en"},
    )
    assert equivalent.status_code == 200, equivalent.text
    assert "The content matches the current revision" in equivalent.text
    assert not re.findall(r"<pre\b[^>]*>.*?</pre>", equivalent.text, re.DOTALL)
    assert 'action="/dashboard/rollback"' not in equivalent.text


def test_legacy_skill_history_remains_readable_without_package_name_compatibility(
    dashboard: TestClient, tmp_path: Path
) -> None:
    content = {
        "name": "compatibility-check",
        "description": "Check compatibility before release",
        "instructions": "Run the approved compatibility tests.",
        "validation": ["Compatibility tests pass"],
    }
    scope = create_scope(dashboard, "Legacy skill name")["scope_id"]
    created = dashboard.post(f"/v1/scopes/{scope}/artifacts", json={"family": "skill", "content": content})
    assert created.status_code == 201, created.text
    skill_id = created.json()["artifact_id"]
    _put_content(dashboard, scope, "skill", skill_id, {**content, "instructions": "Run current tests."})
    # Legacy names allowed spaces and uppercase letters; standard packages do not.
    with sqlite3.connect(tmp_path / "data.db") as connection:
        connection.execute(
            "UPDATE pc_artifacts SET content = ? WHERE scope_id = ? AND family = 'skill' AND artifact_id = ? "
            "AND revision = 1",
            (json.dumps({**content, "name": "Legacy Helper"}).encode(), scope, skill_id),
        )
    opened = dashboard.get(
        "/dashboard/skill",
        params={"scope": scope, "artifact": skill_id, "revision": "1", "compare": "2", "lang": "en"},
    )
    assert opened.status_code == 200, opened.text
    assert "- Legacy Helper" in unescape(opened.text)
    assert "+ compatibility-check" in unescape(opened.text)
    assert "- Run the approved compatibility tests." in unescape(opened.text)
    assert "+ Run current tests." in unescape(opened.text)


def test_dashboard_rolls_back_experience_skill_topic_and_handoff(dashboard: TestClient) -> None:
    scope = create_scope(dashboard, "Family rollback")["scope_id"]
    experience = dashboard.post(
        f"/v1/scopes/{scope}/artifacts",
        json={
            "family": "experience",
            "content": {
                "situation": "A compatibility issue was found before release",
                "action": "Add cross-version tests",
                "outcome": "Avoided a production regression",
                "lesson": "Public API changes require compatibility coverage",
            },
        },
    )
    assert experience.status_code == 201, experience.text
    experience_id = experience.json()["artifact_id"]
    _put_content(
        dashboard,
        scope,
        "experience",
        experience_id,
        {
            "situation": "A compatibility issue was found before release",
            "action": "Add cross-version tests",
            "outcome": "Avoided a production regression",
            "lesson": "A later lesson replaced the approved one",
        },
    )
    restored = _rollback(dashboard, scope, "experience", experience_id, 2)
    assert restored.status_code == 200
    assert "Public API changes require compatibility coverage" in restored.text
    assert dashboard.get(f"/v1/scopes/{scope}/artifacts/experience/{experience_id}").json()["revision"] == 3

    skill = dashboard.post(
        f"/v1/scopes/{scope}/artifacts",
        json={
            "family": "skill",
            "content": {
                "name": "compatibility-check",
                "description": "Check compatibility before release",
                "instructions": "Run the approved compatibility tests.",
                "validation": ["Compatibility tests pass"],
            },
        },
    )
    assert skill.status_code == 201, skill.text
    skill_id = skill.json()["artifact_id"]
    _put_content(
        dashboard,
        scope,
        "skill",
        skill_id,
        {
            "name": "compatibility-check",
            "description": "Check compatibility before release",
            "instructions": "Run a later set of tests.",
            "validation": ["Compatibility tests pass"],
        },
    )
    restored_skill = _rollback(dashboard, scope, "skill", skill_id, 2)
    assert restored_skill.status_code == 200, restored_skill.text
    assert "Run the approved compatibility tests." in restored_skill.text

    topic = dashboard.post(
        f"/v1/scopes/{scope}/artifacts",
        json={
            "family": "topic-memory",
            "content": {"title": "Approved topic", "summary": "Approved summary", "detail": "Approved detail."},
        },
    )
    assert topic.status_code == 201, topic.text
    topic_id = topic.json()["artifact_id"]
    _put_content(
        dashboard,
        scope,
        "topic-memory",
        topic_id,
        {"title": "Later topic", "summary": "Later summary", "detail": "Later detail."},
    )
    restored_topic = _rollback(dashboard, scope, "topic-memory", topic_id, 2)
    assert restored_topic.status_code == 200, restored_topic.text
    assert "Approved detail." in restored_topic.text

    handoff = commit_handoff(dashboard, scope)
    handoff_id = handoff["artifact_id"]
    original = dashboard.get(f"/v1/scopes/{scope}/artifacts/handoff/{handoff_id}").json()["content"]
    source = {"kind": "source", "source_ref": {"name": "content", "source_id": "review"}}
    changed = {
        "schema": original["schema"],
        "objective": "A later objective replaced the approved handoff.",
        "state": [{"text": "Review remains in progress.", "citations": [source]}],
        "disposition": original["disposition"],
        "next_action": {"text": "Continue the review.", "citations": [source]},
        "omissions": [],
    }
    _put_content(dashboard, scope, "handoff", handoff_id, changed)
    confirmation = dashboard.get(
        "/dashboard/handoff-detail",
        params={"scope": scope, "artifact": handoff_id, "revision": "1", "history": "1", "lang": "en"},
    )
    assert confirmation.status_code == 200, confirmation.text
    assert "This creates a new revision" in confirmation.text
    restored_handoff = _rollback(dashboard, scope, "handoff", handoff_id, 2)
    assert restored_handoff.status_code == 200, restored_handoff.text
    assert "Review the HTTP contract" in restored_handoff.text
    assert "A later objective replaced the approved handoff." not in restored_handoff.text


def test_profile_evidence_is_bound_to_the_exact_revision(dashboard):
    scope = create_scope(dashboard, "Profile evidence")["scope_id"]
    response = dashboard.put(
        f"/v1/scopes/{scope}/profile-policy",
        json={"generation_enabled": True, "activation_mode": "automatic", "expected_version": 0},
    )
    assert response.status_code == 200

    class Generator:
        async def generate(self, value):
            return "# Saved preference\n\nUse Chinese."

    dashboard.app.state.application.profiles.generator = Generator()
    source = dashboard.post(f"/v1/scopes/{scope}/sources", json={"content": "I prefer Chinese."}).json()
    generated = dashboard.post("/v1/profile/flush", json={"scope_id": scope})
    assert generated.status_code == 200, generated.text
    profile = dashboard.get("/dashboard/profile", params={"scope": scope})
    evidence_url = next(url for url in _links(profile.text, f"/dashboard/evidence/{source['source_id']}"))
    assert parse_qs(urlsplit(evidence_url).query)["revision"] == ["1"]
    assert "I prefer Chinese." in dashboard.get(evidence_url).text
    unrelated = dashboard.post(f"/v1/scopes/{scope}/sources", json={"content": "Unrelated secret"}).json()
    bad = evidence_url.replace(source["source_id"], unrelated["source_id"])
    rejected = dashboard.get(bad)
    assert rejected.status_code == 404
    assert "Unrelated secret" not in rejected.text


def test_profile_evidence_authentication_recovers_exact_revision(dashboard):
    scope = create_scope(dashboard, "Profile evidence recovery")["scope_id"]
    response = dashboard.put(
        f"/v1/scopes/{scope}/profile-policy",
        json={"generation_enabled": True, "activation_mode": "automatic", "expected_version": 0},
    )
    assert response.status_code == 200

    class Generator:
        async def generate(self, value):
            return (
                "# First profile\n\nUse Chinese."
                if len(value.sources) == 1
                else "# Current profile\n\nKeep the latest preferences."
            )

    dashboard.app.state.application.profiles.generator = Generator()
    source = dashboard.post(f"/v1/scopes/{scope}/sources", json={"content": "I prefer Chinese."}).json()
    generated = dashboard.post("/v1/profile/flush", json={"scope_id": scope})
    assert generated.status_code == 200, generated.text
    second_source = dashboard.post(f"/v1/scopes/{scope}/sources", json={"content": "Keep this history."}).json()
    generated = dashboard.post("/v1/profile/flush", json={"scope_id": scope})
    assert generated.status_code == 200, generated.text

    history = dashboard.get("/dashboard/profile", params={"scope": scope, "view": "history", "lang": "en"})
    profile_url = next(
        url
        for url in _links(history.text, "/dashboard/profile")
        if parse_qs(urlsplit(url).query).get("revision") == ["1"]
    )
    profile = dashboard.get(profile_url)
    evidence_url = next(url for url in _links(profile.text, f"/dashboard/evidence/{source['source_id']}"))
    assert "revision=1" in evidence_url
    token = dashboard.headers.pop("Authorization").removeprefix("Bearer ")
    login = dashboard.get(evidence_url)
    assert login.status_code == 401
    next_field = re.search(r'name="next" value="([^"]+)"', login.text)
    assert next_field is not None
    next_url = unescape(next_field[1])
    assert urlsplit(next_url).path == "/dashboard/profile"
    assert parse_qs(urlsplit(next_url).query)["revision"] == ["1"]
    assert parse_qs(urlsplit(next_url).query)["return_to"][0].startswith("/dashboard/profile?")
    resumed = dashboard.post(
        "/dashboard/session",
        data={"token": token, "next": next_url},
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert resumed.status_code == 303
    assert resumed.headers["location"] == next_url
    resumed_page = dashboard.get(next_url)
    assert "<h1>First profile</h1>" in resumed_page.text
    assert "Current profile" not in resumed_page.text
    assert second_source["source_id"] not in next_url


def test_handoff_download_stays_exact_and_retains_all_citations(dashboard):
    scope = create_scope(dashboard, "Exact downloads")["scope_id"]
    ref = commit_handoff(dashboard, scope)
    path = f"/v1/scopes/{scope}/artifacts/handoff/{ref['artifact_id']}"
    first = dashboard.get(path)
    content = first.json()["content"]
    content["objective"] = "A newer objective"
    for statement in [*content["state"], content["next_action"]]:
        for citation in statement["citations"]:
            reference = citation["source_ref"]
            reference["name"] = reference.pop("source_type")
    replaced = dashboard.put(path, headers={"If-Match": first.headers["etag"]}, json={"content": content})
    assert replaced.status_code == 200, replaced.text
    query = {"scope": scope, "artifact": ref["artifact_id"], "revision": ref["revision"], "lang": "en"}
    detail = dashboard.get("/dashboard/handoff-detail", params=query)
    download_url = _links(detail.text, "/dashboard/handoff-download")[0]
    result = dashboard.get(download_url)
    assert result.status_code == 200
    assert result.headers["content-disposition"] == 'attachment; filename="handoff-r1.md"'
    assert result.headers["content-type"].startswith("text/markdown")
    assert result.headers["cache-control"] == "no-store"
    assert result.headers["x-content-type-options"] == "nosniff"
    decoded = unescape(result.text)
    rendered = unescape(MarkdownIt("commonmark", {"html": False}).render(result.text))
    assert "Review remains in progress." in rendered
    assert "Continue the review." in rendered
    assert "A newer objective" not in rendered
    assert first.json()["content_digest"] in decoded
    assert ref["artifact_id"] in decoded
    assert '"source_id": "review"' in decoded
    assert dashboard.get(download_url).content == result.content
    assert dashboard.get(path).json()["revision"] == 2
    for language in ("zh", "en"):
        assert dashboard.get("/dashboard/handoff-download", params={**query, "lang": language}).status_code == 200
    assert "## 目标" in dashboard.get("/dashboard/handoff-download", params={**query, "lang": "zh"}).text


def test_handoff_directory_return_survives_refresh_and_login(dashboard, monkeypatch):
    scope = create_scope(dashboard, "Download recovery")["scope_id"]
    commit_handoff(dashboard, scope)
    # Supply two collection pages while keeping exact records and authorization real.
    # New Handoff writes use the default singleton identity.
    records = DashboardAPI.records

    async def paged(api, selected_scope, family, *, cursor=None, limit=12, query=None):
        result = await records(api, selected_scope, family, cursor=None, limit=1, query=query)
        if selected_scope == scope and family == "handoff":
            result["next_cursor"] = None if cursor else "saved-page-position"
        return result

    monkeypatch.setattr(DashboardAPI, "records", paged)
    first = dashboard.get("/dashboard/handoff", params={"scope": scope, "lang": "en"})
    next_page = next(url for url in _links(first.text, "/dashboard/handoff") if "cursor=" in url)
    second = dashboard.get(next_page)
    detail_url = _links(second.text, "/dashboard/handoff-detail")[0]
    return_to = parse_qs(urlsplit(detail_url).query)["return_to"][0]
    assert parse_qs(urlsplit(return_to).query)["cursor"] == parse_qs(urlsplit(next_page).query)["cursor"]
    page = dashboard.get(detail_url)
    assert return_to in _links(page.text, "/dashboard/handoff")
    url = _links(page.text, "/dashboard/handoff-download")[0]
    token = dashboard.headers.pop("Authorization").removeprefix("Bearer ")
    login = dashboard.get(url)
    assert login.status_code == 401
    assert "content-disposition" not in login.headers
    next_field = re.search(r'name="next" value="([^"]+)"', login.text)
    assert next_field is not None
    next_url = unescape(next_field[1])
    assert urlsplit(next_url).path == "/dashboard/handoff-detail"
    assert parse_qs(urlsplit(next_url).query)["revision"] == ["1"]
    resumed = dashboard.post(
        "/dashboard/session", data={"token": token, "next": next_url}, headers={"Origin": "http://testserver"}
    )
    assert resumed.status_code == 200
    assert resumed.url.path == "/dashboard/handoff-detail"
    assert return_to in _links(resumed.text, "/dashboard/handoff")
    assert token not in resumed.text
    assert dashboard.get(_links(resumed.text, "/dashboard/handoff-download")[0]).status_code == 200


@pytest.mark.parametrize(
    "destination",
    [
        "https://evil.example/dashboard/profile?scope=S",
        "//evil.example/dashboard/profile?scope=S",
        "/dashboard/profile?scope=S&scope=X",
        "/dashboard/profile?scope=S&next=https://evil.example",
        "/dashboard/handoff-download?scope=S&artifact=A&revision=1",
        "/dashboard/profile?scope=S%0d%0aLocation:evil",
        "/dashboard/profile?scope=" + "S" * 17000,
    ],
)
def test_sign_in_rejects_unsafe_return_targets(dashboard, destination):
    token = dashboard.headers["Authorization"].removeprefix("Bearer ")
    response = dashboard.post(
        "/dashboard/session",
        data={"token": token, "next": destination},
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard/home"


def test_sign_in_preserves_theme_and_language_in_reading_return(dashboard):
    scope = create_scope(dashboard, "Reading preferences")["scope_id"]
    ref = commit_handoff(dashboard, scope)
    target = "/dashboard/handoff-detail?" + urlencode({
        "scope": scope,
        "artifact": ref["artifact_id"],
        "revision": ref["revision"],
        "lang": "en",
        "theme": "dark",
    })
    token = dashboard.headers.pop("Authorization").removeprefix("Bearer ")
    login = dashboard.get(target)
    assert login.status_code == 401
    next_field = re.search(r'name="next" value="([^"]+)"', login.text)
    assert next_field is not None
    next_url = unescape(next_field[1])
    next_query = parse_qs(urlsplit(next_url).query)
    assert next_query["lang"] == ["en"]
    assert next_query["theme"] == ["dark"]
    resumed = dashboard.post(
        "/dashboard/session",
        data={"token": token, "next": next_url},
        headers={"Origin": "http://testserver"},
    )
    assert resumed.status_code == 200
    assert resumed.url.path == "/dashboard/handoff-detail"
    assert resumed.url.query.decode() == urlsplit(next_url).query


def test_invalid_and_missing_downloads_never_become_attachments(dashboard):
    scope = create_scope(dashboard, "Download errors")["scope_id"]
    ref = commit_handoff(dashboard, scope)
    base = {"scope": scope, "artifact": ref["artifact_id"], "revision": "1"}
    for params in ({}, {**base, "revision": "0"}, {**base, "scope": ""}, {**base, "revision": "bad"}):
        result = dashboard.get("/dashboard/handoff-download", params=params)
        assert result.status_code == 422
        assert "content-disposition" not in result.headers
    missing = dashboard.get("/dashboard/handoff-download", params={**base, "revision": "999"})
    assert missing.status_code == 404
    assert "content-disposition" not in missing.headers
    assert "handoff-detail" in missing.text


def test_collection_download_links_use_each_records_revision(dashboard):
    scope = create_scope(dashboard, "Download list")["scope_id"]
    ref = commit_handoff(dashboard, scope)
    page = dashboard.get("/dashboard/handoff", params={"scope": scope, "lang": "en"})
    url = _links(page.text, "/dashboard/handoff-download")[0]
    assert parse_qs(urlsplit(url).query)["artifact"] == [ref["artifact_id"]]
    assert parse_qs(urlsplit(url).query)["revision"] == [str(ref["revision"])]
    assert dashboard.get(url).status_code == 200


def test_login_body_limit_and_invalid_directory_return(dashboard):
    response = dashboard.post(
        "/dashboard/session", content=b"token=" + b"x" * 32768, headers={"Origin": "http://testserver"}
    )
    assert response.status_code == 413
    scope = create_scope(dashboard, "Return boundary")["scope_id"]
    ref = commit_handoff(dashboard, scope)
    for target in (
        "https://evil.example/",
        "/dashboard/handoff?scope=another",
        "/dashboard/handoff?scope=" + scope + "&return_to=/",
    ):
        page = dashboard.get(
            "/dashboard/handoff-detail",
            params={"scope": scope, "artifact": ref["artifact_id"], "revision": "1", "return_to": target},
        )
        assert page.status_code == 200
        download = _links(page.text, "/dashboard/handoff-download")[0]
        assert "return_to" not in parse_qs(urlsplit(download).query)
        assert dashboard.get(download).status_code == 200
