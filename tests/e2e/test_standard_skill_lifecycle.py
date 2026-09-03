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
import zipfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from powercontext.builtin.artifacts.skill import AgentSkillTarget, capture_skill_archive
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import ExternalSkillsConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import DashboardConfig, DashboardScopeConfig, McpConfig, ServerSettings

_SCOPE = "project:standard-skill"


def test_standard_skill_package_review_revision_usage_governance_and_publication(tmp_path: Path) -> None:
    codex_root = tmp_path / "repo" / ".agents" / "skills"
    claude_root = tmp_path / "repo" / ".claude" / "skills"
    app = create_server_app(
        settings=ServerSettings(
            dashboard=DashboardConfig(
                enabled=True,
                scopes=[DashboardScopeConfig(scope_id=_SCOPE, display_name="Standard Skill")],
            ),
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'standard-skill.db'}"),
            external_skills=ExternalSkillsConfig(
                host_id="standard-skill-test",
                targets=(
                    AgentSkillTarget(
                        target_id="codex-project",
                        agent_kind="codex",
                        installation_scope="project",
                        path=codex_root,
                        allow_managed_publish=True,
                    ),
                    AgentSkillTarget(
                        target_id="claude-project",
                        agent_kind="claude_code",
                        installation_scope="project",
                        path=claude_root,
                        allow_managed_publish=True,
                    ),
                ),
            ),
            mcp=McpConfig(enabled=False),
        )
    )
    first_archive = _skill_archive("Run the exact checks.", "reference-search-needle")
    second_archive = _skill_archive("Run the exact checks, then inspect the report.", "successor-search-needle")

    with TestClient(app) as client:
        first_candidate = _propose_package(client, first_archive)
        first_approved = _approve(client, first_candidate)
        first_ref = first_approved["result_artifact"]

        manifest = client.post(
            "/v1/skill/package/manifest",
            json={"scope_id": _SCOPE, "artifact": first_ref},
        )
        download = client.post(
            "/v1/skill/package/download",
            json={"scope_id": _SCOPE, "artifact": first_ref},
        )
        searched_reference = client.post(
            "/v1/skill/library",
            json={"scope_id": _SCOPE, "query": "reference-search-needle", "limit": 20},
        )
        searched_script_body = client.post(
            "/v1/skill/library",
            json={"scope_id": _SCOPE, "query": "script-body-must-not-be-indexed", "limit": 20},
        )

        second_candidate = _propose_package(client, second_archive, target=first_ref)
        second_approved = _approve(client, second_candidate)
        second_ref = second_approved["result_artifact"]
        second_package = second_candidate["proposal"]["package"]

        task_source = client.post(
            "/v1/sources/content",
            json={
                "scope_id": _SCOPE,
                "source_id": "task-outcome-1",
                "content": "The release verification completed successfully.",
            },
        ).json()["source"]
        usage_payload = {
            "scope_id": _SCOPE,
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

        selection = {"scope_id": _SCOPE, "candidate_id": None, "artifact": second_ref}
        codex_published = client.post(
            "/dashboard/skill-projections/publish",
            json={**selection, "target_id": "codex-project"},
        )
        claude_published = client.post(
            "/dashboard/skill-projections/publish",
            json={**selection, "target_id": "claude-project"},
        )

        deprecated = client.post(
            "/v1/skill/lifecycle",
            json={
                "scope_id": _SCOPE,
                "artifact_id": second_ref["artifact_id"],
                "expected_generation": 0,
                "lifecycle_state": "deprecated",
                "replacement_artifact_id": None,
            },
        )
        stale_lifecycle = client.post(
            "/v1/skill/lifecycle",
            json={
                "scope_id": _SCOPE,
                "artifact_id": second_ref["artifact_id"],
                "expected_generation": 0,
                "lifecycle_state": "active",
                "replacement_artifact_id": None,
            },
        )
        active_library = client.post(
            "/v1/skill/library",
            json={"scope_id": _SCOPE, "include_deprecated": False, "limit": 20},
        )
        governed_library = client.post(
            "/v1/skill/library",
            json={"scope_id": _SCOPE, "include_deprecated": True, "limit": 20},
        )

        codex_unpublished = client.post(
            "/dashboard/skill-projections/unpublish",
            json={**selection, "target_id": "codex-project"},
        )
        deprecated_without_override = client.post(
            "/dashboard/skill-projections/publish",
            json={**selection, "target_id": "codex-project"},
        )
        deprecated_with_override = client.post(
            "/dashboard/skill-projections/publish",
            json={**selection, "target_id": "codex-project", "allow_deprecated": True},
        )

        claude_entrypoint = claude_root / "release-verification" / "SKILL.md"
        original_entrypoint = claude_entrypoint.read_bytes()
        claude_entrypoint.write_bytes(original_entrypoint + b"\nlocal drift\n")
        drifted = client.post("/dashboard/skill-projections/status", json=selection)
        drifted_unpublish = client.post(
            "/dashboard/skill-projections/unpublish",
            json={**selection, "target_id": "claude-project"},
        )

        retired = client.post(
            "/v1/skill/lifecycle",
            json={
                "scope_id": _SCOPE,
                "artifact_id": second_ref["artifact_id"],
                "expected_generation": 1,
                "lifecycle_state": "retired",
                "replacement_artifact_id": None,
            },
        )
        reverse_retirement = client.post(
            "/v1/skill/lifecycle",
            json={
                "scope_id": _SCOPE,
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

    assert codex_published.status_code == 200
    assert claude_published.status_code == 200
    assert (codex_root / "release-verification" / "scripts" / "check.sh").read_bytes() == (
        b"#!/bin/sh\n# script-body-must-not-be-indexed\nexit 0\n"
    )
    assert (codex_root / "release-verification" / "scripts" / "check.sh").stat().st_mode & 0o111
    assert not (codex_root / "release-verification" / "powercontext.json").exists()

    assert deprecated.status_code == 200
    assert deprecated.json()["governance_generation"] == 1
    assert stale_lifecycle.status_code == 409
    assert active_library.json()["skills"] == []
    assert governed_library.json()["skills"][0]["governance"]["lifecycle_state"] == "deprecated"
    assert codex_unpublished.status_code == 200
    assert deprecated_without_override.status_code == 422
    assert deprecated_with_override.status_code == 200

    assert drifted.status_code == 200
    assert drifted.json()["targets"][1]["state"] == "drifted"
    assert drifted_unpublish.status_code == 409
    assert claude_entrypoint.read_bytes().endswith(b"local drift\n")
    assert retired.status_code == 200
    assert retired.json()["lifecycle_state"] == "retired"
    assert reverse_retirement.status_code == 422


def _propose_package(
    client: TestClient,
    archive: bytes,
    *,
    target: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = client.post(
        "/v1/skill/package/propose",
        json={
            "scope_id": _SCOPE,
            "archive_base64": base64.b64encode(archive).decode("ascii"),
            "reason": "Review the complete standard package.",
            "target": target,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _approve(client: TestClient, candidate: dict[str, Any]) -> dict[str, Any]:
    response = client.post(
        "/v1/artifact-candidates/approve",
        json={
            "scope_id": _SCOPE,
            "candidate_id": candidate["candidate_id"],
            "expected_version": candidate["version"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _skill_archive(instructions: str, reference_marker: str) -> bytes:
    entrypoint = (
        "---\n"
        "name: release-verification\n"
        "description: Verify a release from exact reviewed evidence.\n"
        "license: Apache-2.0\n"
        "compatibility: Works with Codex and Claude Code.\n"
        "metadata:\n"
        "  owner: release-engineering\n"
        "allowed-tools: Bash Read\n"
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
