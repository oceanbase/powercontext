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

"""Deployment-configured per-Artifact and per-query tag admission limits."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.catalog_changes.models import CatalogChangeProposal as CandidateProposal
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import ArtifactWrite, InvalidBaseAccessRequestError
from powercontext.builtin.review import InvalidCandidateError
from powercontext.builtin.runtime import (
    BuiltinConfig,
    InferenceConfig,
    RuntimeConfig,
    open_builtin_contexts,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.tags import ArtifactTagTarget, TagFilter, TagQuery
from powercontext.http._generated import models
from powercontext.limits import (
    MAX_QUERY_TAG_LABELS,
    MAX_QUERY_TAG_LABELS_CEILING,
    MAX_TARGET_TAGS,
    MAX_TARGET_TAGS_CEILING,
)
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings


def _app(tmp_path: Path, *, artifact: int | None = None, query: int | None = None):
    return create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'tags.db'}"),
            inference=InferenceConfig(generation_model="test"),
            runtime=RuntimeConfig(
                artifact_processing_families=(),
                tag_max_per_artifact=MAX_TARGET_TAGS if artifact is None else artifact,
                tag_max_query_labels=MAX_QUERY_TAG_LABELS if query is None else query,
            ),
            mcp=McpConfig(enabled=False),
        )
    )


def _scope(client: TestClient, key: str = "tag-limits") -> str:
    response = client.post(
        "/v1/scopes",
        json={"title": "tags", "summary": "limit contract", "idempotency_key": key},
    )
    assert response.status_code == 201, response.text
    return response.json()["scope_id"]


def _entry(client: TestClient, scope_id: str) -> tuple[str, str]:
    response = client.post(
        "/v1/memory/remember",
        json={"scope_id": scope_id, "kind": "fact", "text": "A durable fact for tag limits.", "description": "limit"},
    )
    assert response.status_code == 200, response.text
    artifact = response.json()["records"][0]["artifact"]
    return artifact[
        "artifact_id"
    ], f"/v1/scopes/{scope_id}/artifacts/{artifact['family']}/{artifact['artifact_id']}/tags"


def _replace(client: TestClient, path: str, tags: list[str], etag: str):
    return client.put(path, json={"tags": tags}, headers={"If-Match": etag})


def _etag(client: TestClient, path: str) -> str:
    response = client.get(path)
    assert response.status_code == 200, response.text
    return response.headers["ETag"]


def test_default_artifact_limit_is_unchanged(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        scope = _scope(client)
        _, path = _entry(client, scope)
        accepted = _replace(client, path, [f"label-{index}" for index in range(MAX_TARGET_TAGS)], _etag(client, path))
        rejected = _replace(
            client, path, [f"label-{index}" for index in range(MAX_TARGET_TAGS + 1)], _etag(client, path)
        )

    assert accepted.status_code == 200, accepted.text
    assert rejected.status_code == 422, rejected.text
    assert f"{MAX_TARGET_TAGS}-label Artifact limit" in rejected.text


def test_configured_artifact_limit_allows_more_tags(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, artifact=40)) as client:
        scope = _scope(client)
        _, path = _entry(client, scope)
        accepted = _replace(client, path, [f"label-{index}" for index in range(40)], _etag(client, path))
        rejected = _replace(client, path, [f"label-{index}" for index in range(41)], _etag(client, path))

    assert accepted.status_code == 200, accepted.text
    assert rejected.status_code == 422, rejected.text
    assert "40-label Artifact limit" in rejected.text


def test_query_limit_applies_to_tag_queries(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        scope = _scope(client)
        accepted = client.post(
            f"/v1/scopes/{scope}/artifact-tags/query",
            json={"tags": [f"label-{index}" for index in range(MAX_QUERY_TAG_LABELS)]},
        )
        rejected = client.post(
            f"/v1/scopes/{scope}/artifact-tags/query",
            json={"tags": [f"label-{index}" for index in range(MAX_QUERY_TAG_LABELS + 1)]},
        )

    assert accepted.status_code == 200, accepted.text
    assert rejected.status_code == 422, rejected.text
    assert f"{MAX_QUERY_TAG_LABELS}-label query limit" in rejected.text


def test_configured_query_limit_applies_to_artifact_filters(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, query=20)) as client:
        scope = _scope(client)
        accepted = client.get(
            f"/v1/scopes/{scope}/artifacts/atomic-memory",
            params=[("tag", f"label-{index}") for index in range(20)],
        )
        rejected = client.get(
            f"/v1/scopes/{scope}/artifacts/atomic-memory",
            params=[("tag", f"label-{index}") for index in range(21)],
        )

    assert accepted.status_code == 200, accepted.text
    assert rejected.status_code == 400, rejected.text
    assert "20-label query limit" in rejected.text


def test_query_limit_applies_to_memory_search_filters(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        scope = _scope(client)
        body = {"query": "fact", "tag_filter": {"tags": [f"label-{index}" for index in range(17)]}}
        rejected = client.post("/v1/memory/search", json={"scope_id": scope, **body})
        accepted = client.post(
            "/v1/memory/search",
            json={"scope_id": scope, "query": "fact", "tag_filter": {"tags": ["label-0"]}},
        )

    assert rejected.status_code == 422, rejected.text
    assert f"{MAX_QUERY_TAG_LABELS}-label query limit" in rejected.text
    assert accepted.status_code == 200, accepted.text


def test_lowering_the_artifact_limit_keeps_existing_tags_readable_and_replaceable(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, artifact=40)) as client:
        scope = _scope(client)
        _, path = _entry(client, scope)
        original = [f"label-{index}" for index in range(40)]
        assert _replace(client, path, original, _etag(client, path)).status_code == 200

    with TestClient(_app(tmp_path, artifact=10)) as client:
        readable = client.get(path)
        lowered = _replace(client, path, [f"label-{index}" for index in range(10)], readable.headers["ETag"])
        rejected = _replace(client, path, [f"label-{index}" for index in range(11)], lowered.headers["ETag"])
        cleared = _replace(client, path, [], lowered.headers["ETag"])

    assert readable.status_code == 200, readable.text
    assert sorted(readable.json()["tags"]) == sorted(original)
    assert lowered.status_code == 200, lowered.text
    assert rejected.status_code == 422, rejected.text
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["tags"] == []


def test_configuration_above_the_ceiling_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RuntimeConfig(tag_max_per_artifact=MAX_TARGET_TAGS_CEILING + 1)
    with pytest.raises(ValidationError):
        RuntimeConfig(tag_max_query_labels=MAX_QUERY_TAG_LABELS_CEILING + 1)


def test_atomic_memory_routes_apply_the_query_limit(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, query=2)) as client:
        scope = _scope(client)
        listed = client.post("/v1/atomic-memory/list", json={"scope_id": scope, "tags": ["a", "b", "c"]})
        searched = client.post(
            "/v1/atomic-memory/search", json={"scope_id": scope, "query": "fact", "tags": ["a", "b", "c"]}
        )

    assert listed.status_code == 422, listed.text
    assert "2-label query limit" in listed.text
    assert searched.status_code == 422, searched.text
    assert "2-label query limit" in searched.text


def test_candidate_proposal_accepts_labels_above_the_default_limit() -> None:
    labels = [f"label-{index}" for index in range(40)]
    proposal = models.CatalogChangeProposal.model_validate({
        "target": {"type": "artifact", "family": "profile", "artifact_id": "candidate"},
        "expected_etag": "etag",
        "basis_ref": {"family": "profile", "artifact_id": "basis", "revision": 1},
        "before_tags": labels,
        "after_tags": labels,
    })

    assert len(proposal.before_tags) == 40
    assert len(proposal.after_tags) == 40


def test_embedded_runtime_queries_apply_the_query_limit(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'embedded.db'}"),
            runtime=RuntimeConfig(artifact_processing_families=(), tag_max_query_labels=2),
        )
        async with open_builtin_runtime(config) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Embedded limits", summary="query limit", idempotency_key="embedded-limits")
            )
            records = runtime.records.for_scope(scope.scope_id)
            with pytest.raises(InvalidBaseAccessRequestError, match="2-label query limit"):
                await records.query_tags(TagQuery(tags=("one", "two", "three")))
            with pytest.raises(InvalidBaseAccessRequestError, match="2-label query limit"):
                await records.query_artifacts(
                    "profile", limit=5, cursor=None, tag_filter=TagFilter(tags=("one", "two", "three"))
                )

    asyncio.run(scenario())


def _candidate_proposal(
    client: TestClient, scope_id: str, artifact_id: str, path: str, after_tags: list[str]
) -> CandidateProposal:
    del scope_id
    current = client.get(path)
    assert current.status_code == 200, current.text
    return CandidateProposal(
        target=ArtifactTagTarget(family="atomic-memory", artifact_id=artifact_id),
        expected_etag=current.headers["ETag"],
        basis_ref=ArtifactRef(family="atomic-memory", artifact_id=artifact_id, revision=1),
        before_tags=tuple(current.json()["tags"]),
        after_tags=tuple(after_tags),
    )


def test_candidate_larger_than_the_default_limit_approves_over_http(tmp_path: Path) -> None:
    labels = [f"label-{index}" for index in range(40)]
    with TestClient(_app(tmp_path, artifact=64)) as client:
        scope = _scope(client)
        artifact_id, path = _entry(client, scope)
        support_id, _ = _entry(client, scope)
        app = client.app
        assert isinstance(app, FastAPI)
        service = app.state.application.catalog_changes.for_scope(scope)
        candidate = asyncio.run(
            service.propose(
                _candidate_proposal(client, scope, artifact_id, path, labels),
                reason="forty labels",
                artifacts=(ArtifactRef(family="atomic-memory", artifact_id=support_id, revision=1),),
            )
        )
        candidate_id = candidate.candidate_id

        fetched = client.post("/v1/candidates/get", json={"scope_id": scope, "candidate_id": candidate_id})
        assert fetched.status_code == 200, fetched.text
        listed = client.post("/v1/candidates/list", json={"scope_id": scope})
        assert listed.status_code == 200, listed.text
        assert candidate_id in {item["candidate_id"] for item in listed.json()["candidates"]}
        approved = client.post(
            "/v1/candidates/approve",
            json={"scope_id": scope, "candidate_id": candidate_id, "expected_version": 1},
        )
        assert approved.status_code == 200, approved.text
        current = client.get(path)
        assert current.status_code == 200
        assert set(current.json()["tags"]) == set(labels)


def test_candidate_proposal_and_revision_respect_the_effective_limit(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        scope = _scope(client)
        artifact_id, path = _entry(client, scope)
        support_id, _ = _entry(client, scope)
        app = client.app
        assert isinstance(app, FastAPI)
        service = app.state.application.catalog_changes.for_scope(scope)
        with pytest.raises(InvalidCandidateError, match="32-tag"):
            asyncio.run(
                service.propose(
                    _candidate_proposal(client, scope, artifact_id, path, [f"label-{index}" for index in range(33)]),
                    reason="over limit",
                    artifacts=(ArtifactRef(family="atomic-memory", artifact_id=support_id, revision=1),),
                )
            )
        candidate = asyncio.run(
            service.propose(
                _candidate_proposal(client, scope, artifact_id, path, [f"label-{index}" for index in range(32)]),
                reason="at limit",
                artifacts=(ArtifactRef(family="atomic-memory", artifact_id=support_id, revision=1),),
            )
        )
        with pytest.raises(InvalidCandidateError, match="32-tag"):
            asyncio.run(
                service.revise(
                    candidate.candidate_id,
                    1,
                    after_tags=tuple(f"label-{index}" for index in range(33)),
                    reason="over limit revision",
                )
            )


def test_lowering_the_limit_keeps_candidates_readable_but_not_approvable(tmp_path: Path) -> None:
    labels = [f"label-{index}" for index in range(40)]
    with TestClient(_app(tmp_path, artifact=64)) as client:
        scope = _scope(client)
        artifact_id, path = _entry(client, scope)
        support_id, _ = _entry(client, scope)
        app = client.app
        assert isinstance(app, FastAPI)
        service = app.state.application.catalog_changes.for_scope(scope)
        candidate = asyncio.run(
            service.propose(
                _candidate_proposal(client, scope, artifact_id, path, labels),
                reason="forty labels",
                artifacts=(ArtifactRef(family="atomic-memory", artifact_id=support_id, revision=1),),
            )
        )
        candidate_id = candidate.candidate_id

    with TestClient(_app(tmp_path, artifact=32)) as client:
        fetched = client.post("/v1/candidates/get", json={"scope_id": scope, "candidate_id": candidate_id})
        assert fetched.status_code == 200, fetched.text
        rejected = client.post(
            "/v1/candidates/approve",
            json={"scope_id": scope, "candidate_id": candidate_id, "expected_version": 1},
        )
        assert rejected.status_code == 422, rejected.text
        assert "after_tags" in rejected.text
        assert "tag_limit_exceeded" in rejected.text
        assert '"limit":32' in rejected.text
        current = client.get(path)
        assert current.status_code == 200
        assert current.json()["tags"] == []


def test_embedded_runtime_rejects_tag_writes_above_the_configured_limit(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'write-limit.db'}"),
            runtime=RuntimeConfig(artifact_processing_families=(), tag_max_per_artifact=2),
        )
        async with open_builtin_runtime(config) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Write limit", summary="embedded write", idempotency_key="write-limit")
            )
            records = runtime.records.for_scope(scope.scope_id)
            created = await records.create_artifact(
                "profile", ArtifactWrite(content={"content": "# Profile\n\nWrite limit."})
            )
            target = ArtifactTagTarget(family="profile", artifact_id=created.artifact_id)
            current = await records.get_tags(target)
            with pytest.raises(InvalidBaseAccessRequestError, match="2-label"):
                await records.replace_tags(target, ("one", "two", "three"), expected_etag=current.etag)

    asyncio.run(scenario())


def test_export_survives_a_lowered_write_limit(tmp_path: Path) -> None:
    archive = tmp_path / "lowered.pcb"
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'lowered.db'}"

    async def scenario() -> None:
        async def authorize(_scopes: tuple[str, ...]) -> None:
            pass

        async with open_builtin_contexts(
            BuiltinConfig(
                database=SQLiteConfig(url=database_url),
                runtime=RuntimeConfig(artifact_processing_families=(), tag_max_per_artifact=64),
            )
        ) as contexts:
            scope = await contexts.scopes.create(
                ScopeDraft(title="Export limit", summary="lowered", idempotency_key="export-limit")
            )
            created = await contexts.records.create_artifact(
                scope.scope_id, "profile", ArtifactWrite(content={"content": "# Profile\n\nExport limit."})
            )
            target = ArtifactTagTarget(family="profile", artifact_id=created.artifact_id)
            current = await contexts.records.get_tags(scope.scope_id, target)
            await contexts.records.replace_tags(
                scope.scope_id, target, tuple(f"label-{index}" for index in range(40)), expected_etag=current.etag
            )
            await contexts.portability.export([scope.scope_id], archive, authorize=authorize)

        async with open_builtin_contexts(
            BuiltinConfig(
                database=SQLiteConfig(url=database_url),
                runtime=RuntimeConfig(artifact_processing_families=(), tag_max_per_artifact=32),
            )
        ) as contexts:
            second = tmp_path / "lowered-second.pcb"
            await contexts.portability.export([scope.scope_id], second, authorize=authorize)
            assert second.exists()

    asyncio.run(scenario())
