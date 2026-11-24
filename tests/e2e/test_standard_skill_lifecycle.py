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

import base64
import io
import json
import sqlite3
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from powercontext.builtin.artifacts.generation import ArtifactGenerationInput
from powercontext.builtin.artifacts.skill import (
    LLMSkillGenerator,
    SkillContent,
    SkillGenerationOutput,
    build_instruction_skill_package,
    capture_skill_archive,
)
from powercontext.builtin.inference.pydantic_ai import PydanticAIStructuredGenerator
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings


def test_standard_skill_package_review_revision_usage_and_governance(tmp_path: Path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'standard-skill.db'}"),
            mcp=McpConfig(enabled=False),
        )
    )
    first_archive = _skill_archive("Run the exact checks.", "reference-search-needle")
    second_archive = _skill_archive("Run the exact checks, then inspect the report.", "successor-search-needle")

    with TestClient(app) as client:
        scope_id = client.post(
            "/v1/scopes",
            json={"title": "Standard Skill", "summary": "Standard Skill lifecycle", "idempotency_key": "standard"},
        ).json()["scope_id"]
        first_candidate = _propose_package(client, scope_id, first_archive)
        first_approved = _approve(client, scope_id, first_candidate)
        first_ref = first_approved["result_artifact"]

        manifest = client.post(
            "/v1/skill/package/manifest",
            json={"scope_id": scope_id, "artifact": first_ref},
        )
        download = client.post(
            "/v1/skill/package/download",
            json={"scope_id": scope_id, "artifact": first_ref},
        )
        searched_reference = client.post(
            "/v1/skill/library",
            json={"scope_id": scope_id, "query": "reference-search-needle", "limit": 20},
        )
        searched_script_body = client.post(
            "/v1/skill/library",
            json={"scope_id": scope_id, "query": "script-body-must-not-be-indexed", "limit": 20},
        )

        second_candidate = _propose_package(client, scope_id, second_archive, target=first_ref)
        second_approved = _approve(client, scope_id, second_candidate)
        second_ref = second_approved["result_artifact"]
        second_package = second_candidate["proposal"]["package"]

        task_source = client.post(
            "/v1/sources/content",
            json={
                "scope_id": scope_id,
                "source_id": "task-outcome-1",
                "content": "The release verification completed successfully.",
            },
        ).json()["source"]
        usage_payload = {
            "scope_id": scope_id,
            "observation_id": "usage-1",
            "skill_ref": second_ref,
            "package_digest": f"sha256:{second_package['tree_digest']}",
            "target_id": "codex-project",
            "selected": True,
            "invoked": "true",
            "validation": "passed",
            "outcome": "success",
            "task_source": task_source,
            "environment_fingerprint": f"sha256:{'a' * 64}",
        }
        usage = client.post("/v1/skill/usage", json=usage_payload)
        usage_retry = client.post("/v1/skill/usage", json=usage_payload)
        usage_conflict = client.post(
            "/v1/skill/usage",
            json={**usage_payload, "outcome": "failure"},
        )
        usage_wrong_digest = client.post(
            "/v1/skill/usage",
            json={**usage_payload, "observation_id": "usage-wrong", "package_digest": f"sha256:{'b' * 64}"},
        )

        deprecated = client.post(
            "/v1/skill/lifecycle",
            json={
                "scope_id": scope_id,
                "artifact_id": second_ref["artifact_id"],
                "expected_generation": 0,
                "lifecycle_state": "deprecated",
                "replacement_artifact_id": None,
            },
        )
        stale_lifecycle = client.post(
            "/v1/skill/lifecycle",
            json={
                "scope_id": scope_id,
                "artifact_id": second_ref["artifact_id"],
                "expected_generation": 0,
                "lifecycle_state": "active",
                "replacement_artifact_id": None,
            },
        )
        active_library = client.post(
            "/v1/skill/library",
            json={"scope_id": scope_id, "include_deprecated": False, "limit": 20},
        )
        governed_library = client.post(
            "/v1/skill/library",
            json={"scope_id": scope_id, "include_deprecated": True, "limit": 20},
        )

        retired = client.post(
            "/v1/skill/lifecycle",
            json={
                "scope_id": scope_id,
                "artifact_id": second_ref["artifact_id"],
                "expected_generation": 1,
                "lifecycle_state": "retired",
                "replacement_artifact_id": None,
            },
        )
        reverse_retirement = client.post(
            "/v1/skill/lifecycle",
            json={
                "scope_id": scope_id,
                "artifact_id": second_ref["artifact_id"],
                "expected_generation": 2,
                "lifecycle_state": "active",
                "replacement_artifact_id": None,
            },
        )

    assert first_candidate["proposal"]["package"]["file_count"] == 3
    assert manifest.status_code == 200
    assert [(item["path"], item["executable"]) for item in manifest.json()["files"]] == [
        ("SKILL.md", False),
        ("references/runbook.md", False),
        ("scripts/check.sh", True),
    ]
    assert download.status_code == 200
    downloaded = capture_skill_archive(base64.b64decode(download.json()["archive_base64"], validate=True))
    assert downloaded.reference.model_dump(mode="json") == first_candidate["proposal"]["package"]
    assert len(searched_reference.json()["skills"]) == 1
    assert searched_script_body.json()["skills"] == []

    assert second_ref == {**first_ref, "revision": first_ref["revision"] + 1}
    assert second_candidate["target"] == first_ref
    assert second_candidate["artifact_refs"] == [first_ref]
    assert second_package["tree_digest"] != first_candidate["proposal"]["package"]["tree_digest"]

    assert usage.status_code == 201
    assert usage.json()["source"] == {"name": "skill-usage", "source_id": "usage-1"}
    assert usage_retry.status_code == 201
    assert usage_retry.json()["position"] == usage.json()["position"]
    assert usage_conflict.status_code == 409
    assert usage_wrong_digest.status_code == 422

    assert deprecated.status_code == 200
    assert deprecated.json()["governance_generation"] == 1
    assert stale_lifecycle.status_code == 409
    assert active_library.json()["skills"] == []
    assert governed_library.json()["skills"][0]["governance"]["lifecycle_state"] == "deprecated"
    assert retired.status_code == 200
    assert retired.json()["lifecycle_state"] == "retired"
    assert reverse_retirement.status_code == 422


def _propose_package(
    client: TestClient,
    scope_id: str,
    archive: bytes,
    *,
    target: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = client.post(
        "/v1/skill/package/propose",
        json={
            "scope_id": scope_id,
            "archive_base64": base64.b64encode(archive).decode("ascii"),
            "reason": "Review the complete standard package.",
            "target": target,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _approve(client: TestClient, scope_id: str, candidate: dict[str, Any]) -> dict[str, Any]:
    response = client.post(
        "/v1/candidates/approve",
        json={
            "scope_id": scope_id,
            "candidate_id": candidate["candidate_id"],
            "expected_version": candidate["version"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _skill_archive(instructions: str, reference_marker: str, *, allowed_tools: str = "Bash Read") -> bytes:
    entrypoint = (
        "---\n"
        "name: release-verification\n"
        "description: Verify a release from exact reviewed evidence.\n"
        "license: Apache-2.0\n"
        "compatibility: Works with Codex and Claude Code.\n"
        "metadata:\n"
        "  owner: release-engineering\n"
        f"allowed-tools: {allowed_tools}\n"
        "---\n\n"
        f"{instructions}\n"
    ).encode()
    files = {
        "SKILL.md": (entrypoint, 0o100644),
        "references/runbook.md": (f"Release runbook: {reference_marker}\n".encode(), 0o100644),
        "scripts/check.sh": (b"#!/bin/sh\n# script-body-must-not-be-indexed\nexit 0\n", 0o100755),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, (content, mode) in reversed(tuple(files.items())):
            entry = zipfile.ZipInfo(path)
            entry.external_attr = mode << 16
            archive.writestr(entry, content)
    return buffer.getvalue()


@pytest.fixture
def historical_skill() -> SkillContent:
    return SkillContent(
        name="historical-tools",
        description="Inspect historical tools.",
        instructions="Inspect the source.",
        validation=("Source checked.",),
        allowed_tools="Bash(git:*",
    )


@pytest.fixture
def skill_write_api(
    tmp_path: Path, historical_skill: SkillContent
) -> Iterator[tuple[TestClient, Path, dict[str, Any]]]:
    database = tmp_path / "skills.db"
    model_response = json.dumps({"proposal": historical_skill.model_dump(mode="json")})
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"), mcp=McpConfig(enabled=False)
        ),
        skill_generator=LLMSkillGenerator(
            PydanticAIStructuredGenerator(
                model=FunctionModel(lambda _messages, _info: ModelResponse(parts=[TextPart(model_response)])),
                instructions="Revise the instruction Skill.",
                input_type=ArtifactGenerationInput,
                output_type=SkillGenerationOutput,
            )
        ),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        scope = client.post(
            "/v1/scopes", json={"title": "Tools", "summary": "Skill writes", "idempotency_key": "tools"}
        ).json()["scope_id"]
        source = client.post(
            "/v1/sources/content",
            json={"scope_id": scope, "source_id": "feedback", "content": "Improve the procedure."},
        ).json()["source"]
        yield client, database, {"scope_id": scope, "source_refs": [source], "artifact_refs": []}


@pytest.mark.parametrize(("declaration", "expected"), [("Bash(git:*", 422), ("Read) Bash", 201)])
def test_skill_new_writes_validate_declarations(
    skill_write_api: tuple[TestClient, Path, dict[str, Any]],
    historical_skill: SkillContent,
    declaration: str,
    expected: int,
) -> None:
    client, database, lineage = skill_write_api
    scope = lineage["scope_id"]
    content = historical_skill.model_copy(update={"allowed_tools": declaration})
    payload = content.model_dump(mode="json")
    created = client.post(f"/v1/scopes/{scope}/artifacts", json={"family": "skill", "content": payload})
    uploaded = client.post(
        "/v1/skill/package/propose",
        json={
            "scope_id": scope,
            "archive_base64": base64.b64encode(build_instruction_skill_package(content).archive_bytes).decode(),
        },
    )
    proposed = client.post("/v1/skill/propose", json={**lineage, "proposal": payload})
    for response in (created, uploaded, proposed):
        assert response.status_code == expected, response.text
        if expected == 422:
            details = response.json()["error"]["details"]
            assert details["field"] == "allowed_tools"
            assert f": {declaration!r}; unparsed=({declaration!r},)" in details["reason"]
    if expected == 422:
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT count(*) FROM pc_skill_packages").fetchone()[0] == 0


@pytest.mark.parametrize("package_backed", [False, True])
def test_historical_tool_declarations_support_reads_and_exact_restore(
    skill_write_api: tuple[TestClient, Path, dict[str, Any]], historical_skill: SkillContent, package_backed: bool
) -> None:
    client, database, lineage = skill_write_api
    old = historical_skill
    snapshot = build_instruction_skill_package(old)
    historical = snapshot.as_skill_content() if package_backed else old
    scope = lineage["scope_id"]
    collection = f"/v1/scopes/{scope}/artifacts"
    valid = old.model_copy(update={"allowed_tools": "Bash Read"}).model_dump(mode="json")
    created = client.post(collection, json={"family": "skill", "content": valid})
    assert created.status_code == 201, created.text
    artifact_id = created.json()["artifact_id"]
    path = f"{collection}/skill/{artifact_id}"
    target = {"family": "skill", "artifact_id": artifact_id, "revision": 1}

    # Seed a pre-upgrade revision, then exercise writes through the public API.
    with sqlite3.connect(database) as connection:
        if package_backed:
            connection.execute(
                "INSERT INTO pc_skill_packages "
                "(scope_id, tree_digest, archive_digest, archive_bytes, manifest, file_count, "
                "uncompressed_size, archive_size, created_at) VALUES "
                "(:scope_id, :tree_digest, :archive_digest, :archive_bytes, :manifest, :file_count, "
                ":uncompressed_size, :archive_size, CURRENT_TIMESTAMP)",
                {
                    **snapshot.reference.model_dump(),
                    "scope_id": scope,
                    "archive_bytes": snapshot.archive_bytes,
                    "manifest": snapshot.manifest_bytes,
                },
            )
        connection.execute(
            "UPDATE pc_artifacts SET content = ? WHERE scope_id = ? AND artifact_id = ? AND revision = 1",
            (historical.model_dump_json().encode(), scope, artifact_id),
        )
    original = client.get(path + "/revisions/1")
    assert original.status_code == 200, original.text
    body = original.json()["content"]
    assert body["allowed_tools"] == old.allowed_tools
    if package_backed:
        for operation in ("manifest", "download"):
            result = client.post(f"/v1/skill/package/{operation}", json={"scope_id": scope, "artifact": target})
            assert result.status_code == 200, result.text
    clone = client.post(collection, json={"family": "skill", "content": body})
    assert clone.status_code == 422, clone.text

    _propose_package(client, scope, snapshot.archive_bytes, target=target)
    targeted_lineage = {**lineage, "target": target, "artifact_refs": [target]}
    generated = client.post("/v1/skill/generate", json={**targeted_lineage, "origin": "usage"})
    assert generated.status_code == 200, generated.text
    assert generated.json()["candidate"]["proposal"]["allowed_tools"] == old.allowed_tools
    fresh_generation = client.post("/v1/skill/generate", json={**lineage, "origin": "source"})
    assert fresh_generation.status_code == 422, fresh_generation.text
    assert fresh_generation.json()["error"]["details"]["field"] == "allowed_tools"

    review = {**targeted_lineage, "proposal": body}
    proposed = client.post("/v1/skill/propose", json=review)
    assert proposed.status_code == 201, proposed.text
    revised = client.post(
        "/v1/candidates/revise", json={**review, "candidate_id": proposed.json()["candidate_id"], "expected_version": 1}
    )
    assert revised.status_code == 200, revised.text
    assert _approve(client, scope, revised.json())["result_artifact"]["revision"] == 2
    reviewed = client.get(path)
    assert reviewed.json()["content"]["allowed_tools"] == old.allowed_tools

    inherited = client.put(path, headers={"If-Match": reviewed.headers["ETag"]}, json={"content": body})
    assert inherited.status_code == 200, inherited.text
    headers = {"If-Match": inherited.headers["ETag"]}
    instruction_content = old.model_dump(mode="json")
    modified = client.put(path, headers=headers, json={"content": {**instruction_content, "allowed_tools": "Write("}})
    assert modified.status_code == 422, modified.text
    changed = client.put(
        path,
        headers=headers,
        json={"content": {**instruction_content, "allowed_tools": "Read", "instructions": "Check."}},
    )
    assert changed.status_code == 200, changed.text

    headers = {"If-Match": changed.headers["ETag"]}
    restore = {"content": body, "restored_from_revision": 1, "reason": "Restore the historical declaration."}
    forged = client.put(
        path, headers=headers, json={**restore, "content": {**body, "description": "Different content."}}
    )
    assert forged.status_code == 422, forged.text
    restored = client.put(path, headers=headers, json=restore)
    assert restored.status_code == 200, restored.text
    assert restored.json()["restored_from_revision"] == 1
    assert restored.json()["content"]["allowed_tools"] == old.allowed_tools
    assert client.get(path + "/revisions/1").json() == original.json()


@pytest.mark.parametrize("name", ["Legacy Display Name", "legacy-instruction"])
def test_approve_historical_packageless_skill_candidate(
    skill_write_api: tuple[TestClient, Path, dict[str, Any]], historical_skill: SkillContent, name: str
) -> None:
    client, database, lineage = skill_write_api
    scope = lineage["scope_id"]
    content = historical_skill.model_copy(update={"allowed_tools": None})
    proposed = client.post("/v1/skill/propose", json={**lineage, "proposal": content.model_dump(mode="json")})
    assert proposed.status_code == 201, proposed.text
    candidate = proposed.json()
    legacy = content.model_copy(update={"name": name})
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE pc_candidate_versions SET proposal = ? WHERE scope_id = ? AND candidate_id = ?",
            (legacy.model_dump_json().encode(), scope, candidate["candidate_id"]),
        )
        packages = connection.execute("SELECT count(*) FROM pc_skill_packages").fetchone()[0]
    approved = _approve(client, scope, candidate)
    artifact_id = approved["result_artifact"]["artifact_id"]
    stored = client.get(f"/v1/scopes/{scope}/artifacts/skill/{artifact_id}").json()["content"]
    assert stored["name"] == name
    assert stored["package"] is None
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM pc_skill_packages").fetchone()[0] == packages
