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

"""Observable tag lifecycle through the Server and Python Client."""

import asyncio
from pathlib import Path

import httpx
import pytest

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.runtime.atomic_memory_rebuild import rebuild_atomic_memory_projection
from powercontext.builtin.tags import ArtifactTagTarget, TagFilter
from powercontext.client import PowerContextClient
from powercontext.http import QueryArtifactTagsRequest, ReplaceArtifactTagsRequest
from powercontext.server.authentication import StaticBearerAuthenticationProvider
from powercontext.server.authz import PrincipalRef
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, McpConfig, ServerSettings


class _EmbeddingModel:
    profile = EmbeddingProfile(profile_id="tag-test", model="tag-test", dimension=3)

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        return EmbeddingResult(vectors=tuple((1.0, 0.0, 0.0) for _ in texts))


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", "/artifacts/experience/private/tags", None),
        ("PUT", "/artifacts/experience/private/tags", {"tags": ["private"]}),
        ("GET", "/artifacts/atomic-memory/private/tags", None),
        ("PUT", "/artifacts/atomic-memory/private/tags", {"tags": ["private"]}),
        ("POST", "/artifact-tags/query", {"tags": ["private"]}),
    ],
)
def test_tag_routes_reject_principals_without_access(tmp_path: Path, method: str, path: str, payload) -> None:
    async def scenario() -> None:
        app = create_server_app(
            settings=ServerSettings(
                database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'access.db'}"),
                access=AccessControlConfig(mode="enforced"),
                mcp=McpConfig(enabled=False),
            ),
            authentication_provider=StaticBearerAuthenticationProvider(
                "tag-test-token", PrincipalRef(type="user", id="outsider")
            ),
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client,
        ):
            url = "/v1/scopes/private" + path
            anonymous = await client.request(method, url, json=payload)
            assert anonymous.status_code == 401
            denied = await client.request(
                method,
                url,
                json=payload,
                headers={"Authorization": "Bearer tag-test-token", "If-Match": '"unknown"'},
            )
            assert denied.status_code == 403, denied.text

    asyncio.run(scenario())


def test_tag_search_filters_before_candidate_limits_and_survives_rebuild(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'candidates.db'}"))
        async with open_builtin_contexts(config, embedding_model=_EmbeddingModel()) as contexts:
            service = contexts.atomic_memory.for_scope("project")
            memories = [
                await contexts.records.create_artifact(
                    "project",
                    "atomic-memory",
                    ArtifactWrite(content={"kind": "fact", "text": f"Compatibility test {i:02d}."}),
                )
                for i in range(48)
            ]
            # Equal distances and text ranks put the final identity past top-k.
            # The label must restrict candidates before that window is applied.
            artifact = max(memories, key=lambda item: item.artifact_id)
            target = ArtifactTagTarget(family="atomic-memory", artifact_id=artifact.artifact_id)
            empty = await contexts.records.get_tags("project", target)
            tagged = await contexts.records.replace_tags("project", target, ("chosen",), expected_etag=empty.etag)
            for mode in ("text", "vector", "hybrid"):
                unfiltered = await service.search("compatibility test", mode=mode, limit=32)
                assert len(unfiltered.hits) == 32
                assert artifact.artifact_id not in {hit.hit.artifact_ref.artifact_id for hit in unfiltered.hits}
                result = await service.search(
                    "compatibility test", mode=mode, limit=1, tag_filter=TagFilter(tags=("chosen",))
                )
                assert [hit.hit.artifact_ref.artifact_id for hit in result.hits] == [artifact.artifact_id]
            rebuilt_report = await rebuild_atomic_memory_projection(
                contexts.database,
                contexts.atomic_memory.index,
                embedding_model=_EmbeddingModel(),
                maintenance_confirmed=True,
            )
            assert rebuilt_report.ready, rebuilt_report.errors
            assert await contexts.records.get_tags("project", target) == tagged
            rebuilt = await service.search(
                "compatibility test", mode="text", limit=1, tag_filter=TagFilter(tags=("chosen",))
            )
            assert [hit.hit.artifact_ref.artifact_id for hit in rebuilt.hits] == [artifact.artifact_id]

    asyncio.run(scenario())


def test_http_tags_cover_families_entries_filters_and_inactive_lifecycle(tmp_path: Path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'tags.db'}"), mcp=McpConfig(enabled=False)
        ),
        embedding_model=_EmbeddingModel(),
    )

    asyncio.run(exercise_tag_http(app))


@pytest.mark.parametrize("family", ["profile", "prompt"])
def test_configuration_tags_follow_revisions_and_survive_restart(tmp_path: Path, family: str) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'configuration-tags.db'}"),
            mcp=McpConfig(enabled=False),
        )
    )

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://testserver"
            ) as http,
        ):
            created_scope = await http.post(
                "/v1/scopes",
                json={"title": "Prompt tags", "summary": "Tag boundary regression", "idempotency_key": "prompt-tags"},
            )
            assert created_scope.status_code == 201
            scope = created_scope.json()["scope_id"]
            content = (
                {"content": "# Profile\n\nUse Chinese."}
                if family == "profile"
                else {
                    "schema_version": "powercontext.prompt.v1",
                    "mode": "auto",
                    "instructions": "",
                    "demonstrations": [],
                }
            )
            artifact_id = "profile" if family == "profile" else "memory.extract"
            path = f"/v1/scopes/{scope}/artifacts/{family}/{artifact_id}"
            # Defaults without a saved Artifact do not become implicit tag targets.
            assert (await http.get(path + "/tags")).status_code == 404
            created = await http.post(
                f"/v1/scopes/{scope}/artifacts",
                json={
                    "family": family,
                    "content": content,
                    **({"prompt_key": artifact_id} if family == "prompt" else {}),
                },
            )
            assert created.status_code == 201, created.text
            read = await http.get(path + "/tags")
            assert read.status_code == 200, read.text
            tagged = await http.put(path + "/tags", json={"tags": ["test"]}, headers={"If-Match": read.headers["ETag"]})
            assert tagged.status_code == 200, tagged.text
            original = (await http.get(path)).json()
            revised = await http.put(path, json={"content": content}, headers={"If-Match": created.headers["ETag"]})
            assert revised.status_code == 200 and revised.json()["revision"] == 2, revised.text
            assert (await http.get(path + "/revisions/1")).json() == original
            assert (await http.get(path + "/tags")).headers["ETag"] == tagged.headers["ETag"]
            destination = await http.post(
                "/v1/scopes", json={"title": "Other", "summary": "Isolation", "idempotency_key": "other"}
            )
            other_scope = destination.json()["scope_id"]
            assert (await http.get(path.replace(scope, other_scope) + "/tags")).status_code == 404
            isolated = await http.post(f"/v1/scopes/{other_scope}/artifact-tags/query", json={"tags": ["test"]})
            assert isolated.json()["items"] == []

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as http,
        ):
            current = await http.get(path + "/tags")
            assert current.json() == tagged.json()
            assert current.headers["ETag"] == tagged.headers["ETag"]
            query = await http.post(f"/v1/scopes/{scope}/artifact-tags/query", json={"tags": ["TEST"]})
            assert query.status_code == 200, query.text
            assert [item["reference"] for item in query.json()["items"]] == [
                {"family": family, "artifact_id": artifact_id, "revision": 2}
            ]
            cleared = await http.put(path + "/tags", json={"tags": []}, headers={"If-Match": current.headers["ETag"]})
            assert cleared.status_code == 200 and cleared.json()["tags"] == []
            query = await http.post(
                f"/v1/scopes/{scope}/artifact-tags/query", json={"tags": ["test"], "families": [family]}
            )
            assert query.status_code == 200 and query.json()["items"] == []
            assert (await http.get(path)).json() == revised.json()

    asyncio.run(scenario())


async def exercise_tag_http(app, *, token: str | None = None) -> str:
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers={} if token is None else {"Authorization": f"Bearer {token}"},
        ) as http,
    ):
        client = PowerContextClient("http://testserver", token=token, http_client=http, trust_transport_security=True)
        scope_response = await http.post(
            "/v1/scopes",
            json={
                "title": "Tag acceptance",
                "summary": "Disposable tag acceptance scope",
                "idempotency_key": "tag-test",
            },
        )
        assert scope_response.status_code == 201, scope_response.text
        scope = scope_response.json()["scope_id"]
        source = await http.post(
            f"/v1/scopes/{scope}/sources", json={"source_type": "content", "content": "Tag acceptance evidence"}
        )
        assert source.status_code == 201, source.text
        contents = {
            "profile": {"content": "# Profile\n\nRun tests before release."},
            "prompt": {
                "schema_version": "powercontext.prompt.v1",
                "mode": "auto",
                "instructions": "",
                "demonstrations": [],
            },
            "topic-memory": {"title": "Release", "summary": "Release checks", "detail": "Run tests before release."},
            "atomic-memory": {"kind": "decision", "text": "alpha compatibility check"},
            "experience": {
                "situation": "Compatibility failure",
                "action": "Run tests",
                "outcome": "Passed",
                "lesson": "Test before release",
            },
            "skill": {
                "name": "tag-test",
                "description": "Check release",
                "instructions": "Run compatibility tests",
                "validation": ["Tests pass"],
            },
            "handoff": {
                "schema": "powercontext.handoff.v1",
                "objective": "Tag acceptance",
                "state": [
                    {
                        "text": "Tag acceptance evidence",
                        "citations": [
                            {
                                "kind": "source",
                                "source_ref": {"name": "content", "source_id": source.json()["source_id"]},
                            }
                        ],
                    }
                ],
                "disposition": "complete",
                "next_action": None,
                "omissions": [],
            },
        }
        artifacts = {}
        for family, content in contents.items():
            if family == "atomic-memory":
                for text in ("alpha compatibility check", "alpha fallback check"):
                    remembered = await http.post(
                        "/v1/memory/remember", json={"scope_id": scope, "kind": "decision", "text": text}
                    )
                    assert remembered.status_code == 200, remembered.text
                artifact_id = remembered.json()["records"][0]["artifact"]["artifact_id"]
            else:
                created = await http.post(
                    f"/v1/scopes/{scope}/artifacts",
                    json={
                        "family": family,
                        "content": content,
                        **({"prompt_key": "memory.extract"} if family == "prompt" else {}),
                    },
                )
                assert created.status_code == 201, created.text
                artifact_id = created.json()["artifact_id"]
            artifacts[family] = artifact_id
            path = f"/v1/scopes/{scope}/artifacts/{family}/{artifact_id}"
            before = (await http.get(path)).json()
            current = await client.get_artifact_tags(scope, family, artifact_id)
            assert current is not None and current.tag_set.tags == []
            missing = await http.put(path + "/tags", json={"tags": ["release"]})
            assert missing.status_code == 428
            tagged = await client.replace_artifact_tags(
                scope,
                family,
                artifact_id,
                ReplaceArtifactTagsRequest.model_validate({"tags": ["Release", "客户A"]}),
                expected_etag=current.etag,
            )
            assert (await http.get(path)).json() == before
            assert await client.get_artifact_tags(scope, family, artifact_id, if_none_match=tagged.etag) is None
            conflict = await http.put(path + "/tags", json={"tags": []}, headers={"If-Match": current.etag})
            assert conflict.status_code == 412
            duplicate = await http.put(
                path + "/tags", json={"tags": ["Straße", "STRASSE"]}, headers={"If-Match": tagged.etag}
            )
            assert duplicate.status_code == 422
            reloaded = await client.get_artifact_tags(scope, family, artifact_id)
            assert reloaded is not None and reloaded.etag == tagged.etag
            filtered = await http.get(f"/v1/scopes/{scope}/artifacts/{family}", params={"tag": "release", "limit": 1})
            assert filtered.status_code == 200 and len(filtered.json()["items"]) == 1
            if family == "experience":
                competing = await asyncio.gather(
                    *(
                        http.put(
                            path + "/tags",
                            json={"tags": ["Release", "客户A", label]},
                            headers={"If-Match": tagged.etag},
                        )
                        for label in ("writer-a", "writer-b")
                    )
                )
                assert sorted(response.status_code for response in competing) == [200, 412]
        matches = await client.query_artifact_tags(
            scope, QueryArtifactTagsRequest.model_validate({"tags": ["RELEASE"]})
        )
        assert {item.target.root.family.value for item in matches.items} == set(contents)
        selected = await client.query_artifact_tags(
            scope, QueryArtifactTagsRequest.model_validate({"tags": ["release"], "families": list(contents)})
        )
        assert selected.items == matches.items
        paged = []
        cursor = None
        while True:
            page = await client.query_artifact_tags(
                scope, QueryArtifactTagsRequest.model_validate({"tags": ["release"], "limit": 2, "cursor": cursor})
            )
            paged.extend(page.items)
            cursor = page.next_cursor
            if cursor is None:
                break
        assert paged == matches.items
        destination = await http.post(
            "/v1/scopes",
            json={"title": "Publication target", "summary": "Independent tags", "idempotency_key": "tag-copy"},
        )
        assert destination.status_code == 201
        target_scope = destination.json()["scope_id"]
        published = await http.post(
            "/v1/artifact-publications",
            json={
                "source": {
                    "scope_id": scope,
                    "artifact": {"family": "experience", "artifact_id": artifacts["experience"], "revision": 1},
                },
                "target_scope_id": target_scope,
                "idempotency_key": "tag-copy",
            },
        )
        assert published.status_code == 201, published.text
        copy_id = published.json()["target"]["artifact"]["artifact_id"]
        copy_tags = await client.get_artifact_tags(target_scope, "experience", copy_id)
        assert copy_tags is not None and copy_tags.tag_set.tags == []
        listed = await http.post("/v1/memory/entries/list", json={"scope_id": scope})
        assert listed.status_code == 200, listed.text
        entries = listed.json()["entries"]
        entry = next(item for item in entries if item["artifact"]["artifact_id"] != artifacts["atomic-memory"])
        entry_id = entry["artifact"]["artifact_id"]
        empty = await client.get_artifact_tags(scope, "atomic-memory", entry_id)
        assert empty is not None
        state = await client.replace_artifact_tags(
            scope,
            "atomic-memory",
            entry_id,
            ReplaceArtifactTagsRequest.model_validate({"tags": ["selected"]}),
            expected_etag=empty.etag,
        )
        for mode in ("fts", "vector", "hybrid"):
            response = await http.post(
                "/v1/memory/search",
                json={
                    "scope_id": scope,
                    "query": entry["text"],
                    "mode": mode,
                    "limit": 1,
                    "tag_filter": {"tags": ["SELECTED"]},
                },
            )
            assert response.status_code == 200, response.text
            assert [hit["memory"]["artifact"]["artifact_id"] for hit in response.json()["hits"]] == [entry_id]
        filtered = await http.post(
            "/v1/memory/entries/list", json={"scope_id": scope, "tag_filter": {"tags": ["selected"]}}
        )
        assert [item["artifact"]["artifact_id"] for item in filtered.json()["entries"]] == [entry_id]
        retired = await http.post(
            "/v1/atomic-memory/lifecycle",
            json={
                "scope_id": scope,
                "target": {"artifact": entry["artifact"], "state_version": entry["state_version"]},
                "state": "forgotten",
            },
        )
        assert retired.status_code == 200, retired.text
        reloaded_entry = await client.get_artifact_tags(scope, "atomic-memory", entry_id)
        assert reloaded_entry is not None and reloaded_entry.etag == state.etag
        hidden = await client.query_artifact_tags(
            scope, QueryArtifactTagsRequest.model_validate({"tags": ["selected"]})
        )
        assert hidden.items == []
        inactive = await client.query_artifact_tags(
            scope, QueryArtifactTagsRequest.model_validate({"tags": ["selected"], "include_inactive": True})
        )
        assert len(inactive.items) == 1
        assert (
            inactive.items[0].model_dump(mode="json")["reference"]["revision"]
            == retired.json()["records"][0]["artifact"]["revision"]
        )
        cleared = await client.replace_artifact_tags(
            scope, "atomic-memory", entry_id, ReplaceArtifactTagsRequest(tags=[]), expected_etag=state.etag
        )
        assert cleared.tag_set.tags == []
        return scope
