# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import asyncio
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.middleware import Middleware

from powercontext.artifacts import ArtifactLineage, ArtifactRef
from powercontext.builtin.artifacts.topic_memory import (
    PublishedTopicMemory,
    TopicMemory,
    TopicMemoryBrowseCursor,
    TopicMemoryContent,
    TopicMemoryCurrentItem,
    TopicMemoryDraft,
    prepare_topic_memory_projection,
)
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.sqlite.topic_memory_index import SQLiteTopicMemoryFTSIndex
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.builtin.persistence.topic_memory import TopicMemoryRepository
from powercontext.builtin.persistence.topic_memory_index import CompositeTopicMemoryIndex
from powercontext.builtin.scope import ScopeNotFoundError
from powercontext.server.app import create_app
from powercontext.server.factory import create_server_app
from powercontext.server.middleware import StaticBearerMiddleware
from powercontext.server.settings import DashboardConfig, McpConfig, ServerSettings
from powercontext.server.web import mount_web_ui
from powercontext.sources import SourceRef

_AUTH_HEADERS = {"Authorization": "Bearer dashboard-secret"}
_PUBLISHED_AT = datetime(2026, 9, 6, 1, 2, 3, tzinfo=UTC)
_XSS_TITLE = '<img src=x onerror="globalThis.topicXss=true">'
_XSS_DETAIL = "<script>globalThis.topicDetailXss=true</script>"
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _ref(artifact_id: str, revision: int = 1) -> ArtifactRef:
    return ArtifactRef(family="topic-memory", artifact_id=artifact_id, revision=revision)


def _item(artifact_id: str, *, title: str | None = None) -> TopicMemoryCurrentItem:
    return TopicMemoryCurrentItem(
        artifact_ref=_ref(artifact_id),
        title=artifact_id if title is None else title,
        summary=f"Summary for {artifact_id}",
        published_at=_PUBLISHED_AT,
        source_count=2,
    )


class _ScopedTopics:
    def __init__(self) -> None:
        self.rows = (_item("topic-a", title=_XSS_TITLE), _item("topic-b"), _item("topic-c"))
        self.browse_calls: list[tuple[int, TopicMemoryBrowseCursor | None]] = []
        self.get_calls: list[ArtifactRef] = []

    async def browse(
        self,
        *,
        limit: int,
        after: TopicMemoryBrowseCursor | None = None,
    ) -> tuple[TopicMemoryCurrentItem, ...]:
        self.browse_calls.append((limit, after))
        if after is None:
            return self.rows[:limit]
        assert after.artifact_id == "topic-b"
        assert after.revision == 1
        assert after.published_at == _PUBLISHED_AT
        return self.rows[2:][:limit]

    async def get(self, request) -> PublishedTopicMemory:
        self.get_calls.append(request.artifact)
        artifact = request.artifact
        return PublishedTopicMemory(
            topic=TopicMemory(
                artifact_id=artifact.artifact_id,
                revision=artifact.revision,
                content=TopicMemoryContent(
                    title=_XSS_TITLE,
                    summary="A safe text summary",
                    detail=_XSS_DETAIL,
                ),
                lineage=ArtifactLineage(
                    sources=(
                        SourceRef(source_type="content", source_id="source-1"),
                        SourceRef(source_type="conversation", source_id="source-2"),
                    )
                ),
            ),
            published_at=_PUBLISHED_AT,
            is_current=artifact.revision == 2,
            current_artifact=_ref(artifact.artifact_id, 2),
        )


class _Topics:
    def __init__(self, scoped: _ScopedTopics) -> None:
        self.scoped = scoped
        self.scope_calls: list[str] = []

    def for_scope(self, scope_id: str, /) -> _ScopedTopics:
        self.scope_calls.append(scope_id)
        return self.scoped


class _Scopes:
    def __init__(self) -> None:
        self.rows = {
            "scope-a": SimpleNamespace(scope_id="scope-a", title="Scope A", summary="Scope A", parent_scope_id=None),
            "scope-b": SimpleNamespace(scope_id="scope-b", title="Scope B", summary="Scope B", parent_scope_id=None),
        }

    async def get(self, scope_id: str) -> SimpleNamespace:
        try:
            return self.rows[scope_id]
        except KeyError:
            raise ScopeNotFoundError(scope_id) from None

    async def list(self, *, scope_ids=None) -> tuple[SimpleNamespace, ...]:
        selected = self.rows if scope_ids is None else (scope_id for scope_id in scope_ids if scope_id in self.rows)
        return tuple(self.rows[scope_id] for scope_id in selected)


def _client(
    *,
    authenticated: bool = True,
    handoff_enabled: bool = True,
    root_path: str = "",
) -> tuple[TestClient, _ScopedTopics]:
    scoped = _ScopedTopics()
    token = _AUTH_HEADERS["Authorization"].removeprefix("Bearer ")
    middleware = (Middleware(StaticBearerMiddleware, token=token),) if authenticated else ()
    app = create_app(
        application=SimpleNamespace(topic_memory=_Topics(scoped), scopes=_Scopes()),
        middleware=middleware,
    )
    mount_web_ui(
        app,
        dashboard_enabled=True,
        handoff_report_enabled=handoff_enabled,
        authentication_required=authenticated,
    )
    return TestClient(app, root_path=root_path), scoped


def test_topics_shell_is_public_but_contains_no_configured_or_generated_data() -> None:
    client, _ = _client()

    with client:
        page = client.get("/topics")
        unauthenticated_list = client.post(
            "/dashboard/topic-memories/list",
            json={"scope_id": "scope-a"},
        )
        unauthenticated_get = client.post(
            "/dashboard/topic-memories/get",
            json={"scope_id": "scope-a", "artifact": _ref("topic-a").model_dump(mode="json")},
        )

    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in page.headers["content-security-policy"]
    assert "Scope A" not in page.text
    assert "scope-a" not in page.text
    assert _XSS_TITLE not in page.text
    assert _XSS_DETAIL not in page.text
    assert unauthenticated_list.status_code == 401
    assert unauthenticated_get.status_code == 401


def test_topics_navigation_uses_the_frozen_read_only_order() -> None:
    client, _ = _client()

    with client:
        page = client.get("/topics")
        script = client.get("/static/topics.js")

    navigation = page.text[page.text.index('<nav class="primary-nav"') : page.text.index("</nav>")]
    positions = [navigation.index(label) for label in ("Overview", "Topics", "Skills", "Review", "Handoff Report")]
    assert positions == sorted(positions)
    assert 'aria-current="page" data-i18n="topicsTitle"' in navigation
    for write_label in ("Create", "Edit", "Delete", "Retire", "Publish", "Flush"):
        assert f">{write_label}<" not in page.text
    assert "powercontext-brand-topics-remote-v1" in page.text
    assert "topic-memory-r7-v2" in page.text
    for shared_control_key in (
        "switchDark",
        "switchLight",
        "switchChinese",
        "switchEnglish",
        "languageChinese",
        "languageEnglish",
    ):
        assert script.text.count(f"{shared_control_key}:") == 2


def test_helper_pages_build_urls_from_each_request_host_and_root_path() -> None:
    client, _ = _client(authenticated=False, root_path="/control")
    pages = {
        "/": "/static/dashboard.js",
        "/topics": "/static/topics.js",
        "/skills": "/static/skills.js",
        "/reviews": "/static/review.js",
        "/handoff-reports": "/static/handoff-report.js",
    }
    shared_assets = (
        "/static/powercontext-color.png",
        "/static/powercontext-reverse.png",
        "/static/site.css",
    )
    navigation_paths = ("/", "/topics", "/skills", "/reviews", "/handoff-reports")

    with client:
        for page_path, script_path in pages.items():
            poisoned = client.get(page_path, headers={"Host": "poison.example"})
            victim = client.get(page_path, headers={"Host": "victim.example"})

            assert poisoned.status_code == 200
            assert victim.status_code == 200
            assert "poison.example" not in victim.text
            for asset_path in (*shared_assets, script_path):
                assert f"http://victim.example/control{asset_path}" in victim.text
            for navigation_path in navigation_paths:
                assert f'href="http://victim.example/control{navigation_path}"' in victim.text


@pytest.mark.parametrize(
    ("page_path", "entry_script"),
    (
        ("/", "/static/dashboard.js?v=product-language-v5"),
        ("/skills", "/static/skills.js?v=remote-target-names-topics-v1"),
        ("/reviews", "/static/review.js?v=standard-packages-topics-v1"),
        ("/handoff-reports", "/static/handoff-report.js?v=scope-selection-topics-v1"),
    ),
)
def test_helper_page_templates_reference_the_translation_complete_entry_script(
    page_path: str,
    entry_script: str,
) -> None:
    client, _ = _client(authenticated=False, root_path="/control")

    with client:
        page = client.get(page_path, headers={"Host": "current.example"})

    assert page.status_code == 200
    assert f'<script type="module" src="http://current.example/control{entry_script}"></script>' in page.text


def test_topics_browser_state_regressions_execute_in_node() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the Topics browser-state regressions")
    completed = subprocess.run(
        [
            node,
            "--experimental-default-type=module",
            "--test",
            str(_REPO_ROOT / "tests" / "topics_state_test.mjs"),
        ],
        cwd=_REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_helper_pages_translate_topics_navigation_in_node() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the helper-page navigation regression")
    completed = subprocess.run(
        [
            node,
            "--test",
            str(_REPO_ROOT / "tests" / "helper_page_navigation_test.mjs"),
        ],
        cwd=_REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_private_topic_browse_has_strict_opaque_keyset_pagination() -> None:
    client, scoped = _client()

    with client:
        first = client.post(
            "/dashboard/topic-memories/list",
            headers=_AUTH_HEADERS,
            json={"scope_id": "scope-a", "limit": 2},
        )
        cursor = first.json()["next_cursor"]
        second = client.post(
            "/dashboard/topic-memories/list",
            headers=_AUTH_HEADERS,
            json={"scope_id": "scope-a", "limit": 2, "cursor": cursor},
        )
        invalid = client.post(
            "/dashboard/topic-memories/list",
            headers=_AUTH_HEADERS,
            json={"scope_id": "scope-a", "cursor": "tm1.not-base64!"},
        )

    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    assert [item["artifact"]["artifact_id"] for item in first.json()["items"]] == ["topic-a", "topic-b"]
    assert cursor.startswith("tm1.")
    assert second.status_code == 200
    assert [item["artifact"]["artifact_id"] for item in second.json()["items"]] == ["topic-c"]
    assert second.json()["next_cursor"] is None
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_topic_memory_cursor"
    assert scoped.browse_calls[0] == (3, None)
    assert scoped.browse_calls[1][0] == 3
    assert scoped.browse_calls[1][1] is not None


def test_private_topic_get_returns_exact_historical_state_and_only_source_identifiers() -> None:
    client, scoped = _client()
    exact = _ref("topic-a")

    with client:
        response = client.post(
            "/dashboard/topic-memories/get",
            headers=_AUTH_HEADERS,
            json={"scope_id": "scope-a", "artifact": exact.model_dump(mode="json")},
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "artifact": exact.model_dump(mode="json"),
        "title": _XSS_TITLE,
        "summary": "A safe text summary",
        "detail": _XSS_DETAIL,
        "published_at": "2026-09-06T01:02:03Z",
        "is_current": False,
        "current_artifact": _ref("topic-a", 2).model_dump(mode="json"),
        "source_refs": [
            {"source_type": "content", "source_id": "source-1"},
            {"source_type": "conversation", "source_id": "source-2"},
        ],
    }
    assert scoped.get_calls == [exact]
    assert set(response.json()["source_refs"][0]) == {"source_type", "source_id"}


def test_private_topic_routes_reject_unconfigured_scopes_and_stay_hidden() -> None:
    client, scoped = _client(authenticated=False)

    with client:
        missing_scope = client.post(
            "/dashboard/topic-memories/list",
            json={"scope_id": "scope-private"},
        )
        missing_scope_wrong_family = client.post(
            "/dashboard/topic-memories/get",
            json={
                "scope_id": "scope-private",
                "artifact": {"family": "memory", "artifact_id": "memory", "revision": 1},
            },
        )
        wrong_family = client.post(
            "/dashboard/topic-memories/get",
            json={
                "scope_id": "scope-a",
                "artifact": {"family": "memory", "artifact_id": "memory", "revision": 1},
            },
        )
        openapi = client.get("/openapi.json")

    assert missing_scope.status_code == 404
    assert missing_scope.json()["error"]["code"] == "dashboard_scope_not_found"
    assert missing_scope_wrong_family.status_code == 404
    assert missing_scope_wrong_family.json()["error"]["code"] == "dashboard_scope_not_found"
    assert scoped.browse_calls == []
    assert wrong_family.status_code == 422
    assert "/dashboard/topic-memories/list" not in openapi.json()["paths"]
    assert "/dashboard/topic-memories/get" not in openapi.json()["paths"]


def test_composed_dashboard_browses_current_head_and_reads_an_exact_old_revision(tmp_path) -> None:
    database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'topics.db'}")

    async def seed(scope_id: str) -> tuple[ArtifactRef, ArtifactRef]:
        index = CompositeTopicMemoryIndex(SQLiteTopicMemoryFTSIndex())
        repository = TopicMemoryRepository(index=index)
        async with (
            SQLiteProfile.open(database, tables=BUILTIN_TABLES + index.tables) as profile,
            profile.database.transaction() as connection,
        ):
            await repository.initialize(connection)
            old_content = TopicMemoryContent(
                title="Legacy deployment",
                summary="The first exact revision.",
                detail="Legacy canary detail.",
            )
            old = await repository.publish_create(
                connection,
                scope_id,
                "deployment",
                TopicMemoryDraft(content=old_content),
                prepare_topic_memory_projection(old_content),
            )
            current_content = TopicMemoryContent(
                title="Current deployment",
                summary="The current exact revision.",
                detail="Current canary detail for public relevance search.",
            )
            current = await repository.publish_revision(
                connection,
                scope_id,
                old.topic,
                TopicMemoryDraft(content=current_content),
                prepare_topic_memory_projection(current_content),
            )
        return old.topic.as_ref(), current.topic.as_ref()

    app = create_server_app(
        settings=ServerSettings(
            dashboard=DashboardConfig(enabled=True),
            database=database,
            mcp=McpConfig(enabled=False),
        )
    )

    with TestClient(app) as client:
        scope_id = client.get("/v1/scopes/default").json()["scope_id"]
        old_ref, current_ref = asyncio.run(seed(scope_id))
        page = client.post("/dashboard/topic-memories/list", json={"scope_id": scope_id})
        old = client.post(
            "/dashboard/topic-memories/get",
            json={"scope_id": scope_id, "artifact": old_ref.model_dump(mode="json")},
        )
        search = client.post(
            "/v1/topic-memory/search",
            json={"scope_id": scope_id, "query": "public relevance search", "limit": 20},
        )

    assert page.status_code == 200
    assert old.status_code == 200
    assert search.status_code == 200
    assert [item["artifact"] for item in page.json()["items"]] == [current_ref.model_dump(mode="json")]
    assert old.json()["artifact"] == old_ref.model_dump(mode="json")
    assert old.json()["detail"] == "Legacy canary detail."
    assert old.json()["is_current"] is False
    assert old.json()["current_artifact"] == current_ref.model_dump(mode="json")
    assert [hit["artifact"] for hit in search.json()["hits"]] == [current_ref.model_dump(mode="json")]
