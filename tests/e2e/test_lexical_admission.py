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

"""Lexical qualification must precede bounded candidate selection on real backends."""

import asyncio

import pytest
from fastapi.testclient import TestClient
from pydantic import JsonValue
from sqlalchemy import update

from powercontext.builtin.artifacts.search import AdmissionFloor
from powercontext.builtin.persistence.tables import ARTIFACTS_TABLE
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig, open_builtin_contexts
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, BearerAuthConfig, McpConfig, ServerSettings
from tests.e2e.atomic_memory_migration_backend import MigrationBackend
from tests.e2e.test_topic_memory_generic_api import _scope


def _content(family: str, body: str, number: int) -> dict[str, JsonValue]:
    if family == "experience":
        return {
            "situation": body,
            "action": "Act carefully.",
            "outcome": "Worked.",
            "lesson": "Remember the evidence.",
        }
    return {
        "name": f"skill-{number}",
        "description": "Useful advice",
        "instructions": body,
        "validation": ["Validated"],
    }


def _lexical_app(backend: MigrationBackend):
    return create_server_app(
        settings=ServerSettings(
            database=backend.config,
            runtime=RuntimeConfig(artifact_processing_families=()),
            auth=BearerAuthConfig(enabled=False),
            access=AccessControlConfig(mode="disabled"),
            mcp=McpConfig(enabled=False),
        ),
    )


@pytest.mark.parametrize("family", ["experience", "skill"])
def test_qualified_long_text_survives_high_scoring_low_coverage_matches(
    migration_backend: MigrationBackend, family: str
) -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=migration_backend.config)) as contexts:
            await contexts.get("project")

            target = await contexts.records.create_artifact(
                "project",
                family,
                ArtifactWrite(content=_content(family, "alpha beta " + ("filler " * 300).rstrip(), 999)),
            )
            for number in range(80):
                await contexts.records.create_artifact(
                    "project",
                    family,
                    ArtifactWrite(content=_content(family, "alpha" if number % 2 else "beta", number)),
                )
            if family == "experience":
                result = await contexts.search_experience_outcome("project", "alpha beta gamma", 1)
                hits = result.hits
                relaxed = await contexts.search_experience_outcome(
                    "project",
                    "alpha beta gamma",
                    1,
                    admission=AdmissionFloor(lexical_min_matched_terms=1, lexical_coverage=0),
                )
                assert relaxed.hits and relaxed.hits[0].artifact_ref.artifact_id != target.artifact_id
            else:
                hits = await contexts.search_skills("project", "alpha beta gamma", 1)
            assert len(hits) == 1
            assert hits[0].artifact_ref.artifact_id == target.artifact_id
            assert (
                hits[0]
                .content.model_dump(mode="json")["situation" if family == "experience" else "instructions"]
                .startswith("alpha beta ")
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("family", ["experience", "skill"])
def test_unqualified_corrupt_body_does_not_break_public_search(
    migration_backend: MigrationBackend, family: str
) -> None:
    app = _lexical_app(migration_backend)
    with TestClient(app, raise_server_exceptions=False) as client:
        scope = _scope(client, "unqualified-body")
        created = []
        for number, body in enumerate(("alpha", "alpha beta " + ("filler " * 300).rstrip())):
            response = client.post(
                f"/v1/scopes/{scope}/artifacts", json={"family": family, "content": _content(family, body, number)}
            )
            assert response.status_code == 201, response.text
            created.append(response.json())
        path = f"/v1/scopes/{scope}/artifacts/{family}/search"
        request = {"query": "alpha beta gamma", "limit": 1, "include_scores": True}
        strict = client.post(path, json=request)
        assert strict.status_code == 200, strict.text
        assert strict.json()["results"][0]["artifact_id"] == created[1]["artifact_id"]
        single_term = client.post(path, json={"query": "alpha", "limit": 1})
        assert single_term.status_code == 200, single_term.text
        assert single_term.json()["results"][0]["artifact_id"] == created[0]["artifact_id"]

        async def corrupt_unqualified_body() -> None:
            async with app.state.application.primary_database.transaction() as connection:
                await connection.execute(
                    update(ARTIFACTS_TABLE)
                    .where(
                        ARTIFACTS_TABLE.c.scope_id == scope,
                        ARTIFACTS_TABLE.c.family == family,
                        ARTIFACTS_TABLE.c.artifact_id == created[0]["artifact_id"],
                        ARTIFACTS_TABLE.c.revision == created[0]["revision"],
                    )
                    .values(content=b"not-json")
                )

        assert client.portal is not None
        client.portal.call(corrupt_unqualified_body)
        after = client.post(path, json=request)
        assert after.status_code == 200, after.text
        assert after.json() == strict.json()


@pytest.mark.parametrize("family", ["experience", "skill"])
def test_lower_ranked_rejections_do_not_change_public_top_hit(migration_backend: MigrationBackend, family: str) -> None:
    with TestClient(_lexical_app(migration_backend)) as client:
        scope = _scope(client, "lower-ranked-rejection")
        for number in range(12):
            created = client.post(
                f"/v1/scopes/{scope}/artifacts",
                json={"family": family, "content": _content(family, "alpha beta gamma", number)},
            )
            assert created.status_code == 201, created.text
        path = f"/v1/scopes/{scope}/artifacts/{family}/search"
        request = {"query": "alpha beta gamma", "limit": 1}
        before = client.post(path, json=request)
        assert before.status_code == 200, before.text
        assert len(before.json()["results"]) == 1
        distractor = client.post(
            f"/v1/scopes/{scope}/artifacts",
            json={"family": family, "content": _content(family, "alpha " + ("filler " * 300).rstrip(), 999)},
        )
        assert distractor.status_code == 201, distractor.text
        strict = client.post(path, json=request)
        relaxed = client.post(
            path, json={**request, "admission": {"lexical_min_matched_terms": 1, "lexical_coverage": 0}}
        )
        assert strict.status_code == relaxed.status_code == 200
        assert strict.json() == relaxed.json()
        assert strict.json() == before.json()


@pytest.mark.parametrize("family", ["experience", "skill"])
def test_selected_missing_exact_content_is_a_search_contract_failure(tmp_path, family: str) -> None:
    import sqlite3

    from tests.e2e.test_topic_memory_generic_api import _app

    content = (
        {"situation": "alpha evidence", "action": "Act", "outcome": "Worked", "lesson": "Remember"}
        if family == "experience"
        else {"name": "alpha", "description": "Evidence", "instructions": "alpha evidence", "validation": ["Checked"]}
    )
    with TestClient(_app(tmp_path), raise_server_exceptions=False) as client:
        scope = _scope(client, "missing-selected")
        created = client.post(f"/v1/scopes/{scope}/artifacts", json={"family": family, "content": content})
        assert created.status_code == 201, created.text
        url = f"/v1/scopes/{scope}/artifacts/{family}/search"
        ordinary = client.post(url, json={"query": "alpha", "limit": 1})
        assert ordinary.status_code == 200 and len(ordinary.json()["results"]) == 1
        with sqlite3.connect(tmp_path / "topics.db") as connection:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute(
                "DELETE FROM pc_artifacts WHERE scope_id=? AND family=? AND artifact_id=? AND revision=1",
                (scope, family, created.json()["artifact_id"]),
            )
        damaged = client.post(url, json={"query": "alpha", "limit": 1})
        assert damaged.status_code == 500, damaged.text
