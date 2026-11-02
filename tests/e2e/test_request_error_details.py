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

"""Request validation rejects Dream evidence with an actionable field name."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings


def _artifacts(count: int) -> list[dict[str, object]]:
    return [{"family": "experience", "artifact_id": f"experience-{index}", "revision": 1} for index in range(count)]


def _sources(count: int) -> list[dict[str, str]]:
    return [{"source_type": "content", "source_id": f"source-{index}"} for index in range(count)]


def _tag_target() -> dict[str, object]:
    return {
        "target": {"type": "artifact", "family": "experience", "artifact_id": "experience-1"},
        "expected_etag": "etag-1",
        "basis_ref": {"family": "experience", "artifact_id": "experience-1", "revision": 1},
    }


def _settings(database: Path) -> ServerSettings:
    return ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"),
        mcp=McpConfig(enabled=False),
    )


def test_dream_rejections_name_the_rejected_selection_over_http(tmp_path: Path) -> None:
    app = create_server_app(settings=_settings(tmp_path / "dream-validation.db"))

    async def scenario() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client,
        ):
            empty = await client.post(
                "/v1/scopes/scp_validation/dream",
                json={"operation": "refine_experience", "idempotency_key": "empty"},
            )
            too_many = await client.post(
                "/v1/scopes/scp_validation/dream",
                json={
                    "operation": "refine_experience",
                    "artifacts": _artifacts(21),
                    "idempotency_key": "too-many",
                },
            )
            too_many_references = await client.post(
                "/v1/scopes/scp_validation/dream",
                json={
                    "operation": "refine_experience",
                    "artifacts": _artifacts(20),
                    "sources": _sources(13),
                    "idempotency_key": "too-many-references",
                },
            )

            return empty, too_many, too_many_references

    empty, too_many, too_many_references = asyncio.run(scenario())

    for response, loc, error_type, message in (
        (empty, ["artifacts"], "evidence_selection_required", "1 to 20 Artifacts"),
        (too_many, ["artifacts"], "evidence_limit_exceeded", "at most 20 Artifacts"),
        (too_many_references, ["sources"], "evidence_limit_exceeded", "at most 32 references"),
    ):
        assert response.status_code == 422, response.text
        error = response.json()["error"]
        assert error["code"] == "invalid_request"
        [detail] = error["details"]["errors"]
        assert detail["loc"] == loc
        assert detail["type"] == error_type
        assert message in detail["msg"]
        assert "at most 32 references with Sources" in detail["msg"]


def test_propose_experience_rejection_names_artifact_refs_over_http(tmp_path: Path) -> None:
    app = create_server_app(settings=_settings(tmp_path / "propose-validation.db"))

    async def scenario() -> httpx.Response:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client,
        ):
            scope = await client.post(
                "/v1/scopes",
                json={"title": "Proposal validation", "summary": "Request details", "idempotency_key": "propose"},
            )
            scope_id = scope.json()["scope_id"]
            source = await client.post(
                "/v1/sources/content",
                json={"scope_id": scope_id, "source_id": "evidence", "content": "bounded evidence"},
            )
            artifact = await client.post(
                f"/v1/scopes/{scope_id}/artifacts",
                json={
                    "family": "experience",
                    "content": {"situation": "s", "action": "a", "outcome": "o", "lesson": "l"},
                },
            )
            target = {key: artifact.json()[key] for key in ("family", "artifact_id", "revision")}

            return await client.post(
                "/v1/experience/propose",
                json={
                    "scope_id": scope_id,
                    "proposal": {"situation": "s", "action": "a", "outcome": "o", "lesson": "l"},
                    "source_refs": [source.json()["source"]],
                    "artifact_refs": [],
                    "target": target,
                },
            )

    response = asyncio.run(scenario())

    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "invalid_request"
    assert error["details"] == {"field": "artifact_refs", "reason": "target_not_in_artifact_refs"}
    assert "artifacts" not in str(error["details"])


def test_revise_tags_reports_only_the_combined_limit_over_http(tmp_path: Path) -> None:
    app = create_server_app(settings=_settings(tmp_path / "tag-dream-validation.db"))

    async def scenario() -> tuple[httpx.Response, httpx.Response]:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client,
        ):
            rejected = await client.post(
                "/v1/scopes/scp_validation/dream",
                headers={"X-PowerContext-Dream-Contract": "2"},
                json={
                    "operation": "revise_tags",
                    "tag_target": _tag_target(),
                    "artifacts": _artifacts(21),
                    "sources": _sources(12),
                    "idempotency_key": "tag-too-many",
                },
            )
            passed_validation = await client.post(
                "/v1/scopes/scp_validation/dream",
                headers={"X-PowerContext-Dream-Contract": "2"},
                json={
                    "operation": "revise_tags",
                    "tag_target": _tag_target(),
                    "artifacts": _artifacts(21),
                    "sources": _sources(11),
                    "idempotency_key": "tag-at-the-limit",
                },
            )
            return rejected, passed_validation

    rejected, passed_validation = asyncio.run(scenario())

    assert rejected.status_code == 422, rejected.text
    error = rejected.json()["error"]
    assert error["code"] == "invalid_request"
    [detail] = error["details"]["errors"]
    assert detail["loc"] == ["sources"]
    assert detail["type"] == "evidence_limit_exceeded"
    assert detail["msg"] == "artifacts and sources must select at most 32 references in total"
    assert passed_validation.status_code == 404, passed_validation.text
    assert passed_validation.json()["error"]["code"] == "scope_not_found"
