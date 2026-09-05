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

from powercontext.builtin.artifacts.memory import EmbeddingProfile, MemoryEntryInput
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.tags import MemoryEntryTagTarget, TagFilter
from powercontext.client import PowerContextClient
from powercontext.http import QueryArtifactTagsRequest, ReplaceArtifactTagsRequest
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings


class _EmbeddingModel:
    profile = EmbeddingProfile(profile_id="tag-test", model="tag-test", dimension=3)

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        return EmbeddingResult(vectors=tuple((1.0, 0.0, 0.0) for _ in texts))


def test_tag_search_filters_before_candidate_limits_and_survives_rebuild(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'candidates.db'}"))
        async with open_builtin_contexts(config, embedding_model=_EmbeddingModel()) as contexts:
            service = (await contexts.get("project")).artifacts.memory
            memory = await service.remember(
                memory=None,
                entries=tuple(MemoryEntryInput(kind="fact", text=f"Compatibility test {i:02d}.") for i in range(48)),
                mode="append",
            )
            assert memory is not None
            # Equal vector distances and text ranks leave this entry beyond the
            # unfiltered candidate window. Filtering after top-k would lose it.
            entry = max(memory.content.manifest.entries, key=lambda item: item.entry_id)
            target = MemoryEntryTagTarget(artifact_id=memory.artifact_id, entry_id=entry.entry_id)
            empty = await contexts.records.get_tags("project", target)
            tagged = await contexts.records.replace_tags("project", target, ("chosen",), expected_etag=empty.etag)
            for mode in ("fts", "vector", "hybrid"):
                unfiltered = await service.search("compatibility test", memories=(memory,), mode=mode, limit=32)
                assert len(unfiltered.hits) == 32
                assert entry.entry_id not in {hit.entry_id for hit in unfiltered.hits}
                result = await service.search(
                    "compatibility test", memories=(memory,), mode=mode, limit=1, tag_filter=TagFilter(tags=("chosen",))
                )
                assert [hit.entry_id for hit in result.hits] == [entry.entry_id]
            await service.rebuild_projections()
            assert await contexts.records.get_tags("project", target) == tagged
            rebuilt = await service.search(
                "compatibility test", memories=(memory,), mode="fts", limit=1, tag_filter=TagFilter(tags=("chosen",))
            )
            assert [hit.entry_id for hit in rebuilt.hits] == [entry.entry_id]

    asyncio.run(scenario())


def test_http_tags_cover_families_entries_filters_and_inactive_lifecycle(tmp_path: Path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'tags.db'}"), mcp=McpConfig(enabled=False)
        ),
        embedding_model=_EmbeddingModel(),
    )

    asyncio.run(exercise_tag_http(app))


async def exercise_tag_http(app) -> str:
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as http,
    ):
        client = PowerContextClient("http://testserver", http_client=http, trust_transport_security=True)
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
            "memory": {
                "entries": [
                    {"kind": "decision", "text": "alpha compatibility check"},
                    {"kind": "decision", "text": "alpha fallback check"},
                ]
            },
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
            if family == "memory":
                for text in ("alpha compatibility check", "alpha fallback check"):
                    remembered = await http.post(
                        "/v1/memory/remember", json={"scope_id": scope, "kind": "decision", "text": text}
                    )
                    assert remembered.status_code == 200, remembered.text
                artifact_id = remembered.json()["memory"]["artifact_id"]
            else:
                created = await http.post(f"/v1/scopes/{scope}/artifacts", json={"family": family, "content": content})
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
        assert len(matches.items) == 4
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
        memory_id = artifacts["memory"]
        listed = await http.post("/v1/memory/entries/list", json={"scope_id": scope})
        assert listed.status_code == 200, listed.text
        entries = listed.json()["entries"]
        entry = entries[-1]
        entry_id = entry["citation"]["entry_id"]
        empty = await client.get_memory_entry_tags(scope, memory_id, entry_id)
        assert empty is not None
        state = await client.replace_memory_entry_tags(
            scope,
            memory_id,
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
            assert [hit["citation"]["entry_id"] for hit in response.json()["hits"]] == [entry_id]
        filtered = await http.post(
            "/v1/memory/entries/list", json={"scope_id": scope, "tag_filter": {"tags": ["selected"]}}
        )
        assert [item["citation"]["entry_id"] for item in filtered.json()["entries"]] == [entry_id]
        retired = await http.post(
            "/v1/memory/entries/retire",
            json={"scope_id": scope, "citation": entry["citation"], "reason": "Tag lifecycle acceptance"},
        )
        assert retired.status_code == 200, retired.text
        reloaded_entry = await client.get_memory_entry_tags(scope, memory_id, entry_id)
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
            inactive.items[0].model_dump(mode="json")["reference"]["memory_ref"]["revision"]
            == retired.json()["memory"]["revision"]
        )
        cleared = await client.replace_memory_entry_tags(
            scope, memory_id, entry_id, ReplaceArtifactTagsRequest(tags=[]), expected_etag=state.etag
        )
        assert cleared.tag_set.tags == []
        return scope
