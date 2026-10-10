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

"""New requests cannot reintroduce references to archived legacy Memory collections."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings

COLLECTION = {"family": "memory", "artifact_id": "legacy-memory", "revision": 2}
ENTRY = {"memory_ref": COLLECTION, "entry_id": "alpha", "entry_version_id": "alpha-v2"}
PROPOSAL = {
    "situation": "A legacy reference was supplied.",
    "action": "Submitted it with a new request.",
    "outcome": "The request was refused.",
    "lesson": "Cite Atomic Memory revisions instead.",
}


def _observation(citation: dict[str, Any]) -> dict[str, Any]:
    return {
        "scope_id": "{scope}",
        "source_id": "outcome-1",
        "outcome": {
            "objective": "Ship task A.",
            "status": "failed",
            "summary": "Deployment failed.",
            "observations": [{"text": "Deploy failed.", "basis": "verified", "evidence": [citation]}],
        },
    }


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (
            "/v1/experience/propose",
            {"scope_id": "{scope}", "proposal": PROPOSAL, "source_refs": [], "artifact_refs": [COLLECTION]},
        ),
        (
            "/v1/experience/propose",
            {
                "scope_id": "{scope}",
                "proposal": PROPOSAL,
                "source_refs": [],
                "artifact_refs": [],
                "memory_citations": [ENTRY],
            },
        ),
        (
            "/v1/scopes/{scope}/dream",
            {"operation": "refine_experience", "artifacts": [COLLECTION], "idempotency_key": "k"},
        ),
        (
            "/v1/scopes/{scope}/dream",
            {"operation": "refine_experience", "memory_citations": [ENTRY], "idempotency_key": "k"},
        ),
        ("/v1/work/outcomes/record", _observation({"kind": "artifact", "artifact_ref": COLLECTION})),
        ("/v1/work/outcomes/record", _observation({"kind": "memory", "memory_citation": ENTRY})),
    ],
)
def test_new_requests_reject_legacy_memory_references_before_writing(
    tmp_path: Path, path: str, body: dict[str, Any]
) -> None:
    settings = ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'legacy-references.db'}"),
        mcp=McpConfig(enabled=False),
    )
    with TestClient(create_server_app(settings=settings)) as transport:
        scope = transport.get("/v1/scopes/default").json()["scope_id"]
        payload = _with_scope(body, scope)
        for _ in range(2):
            response = transport.post(path.replace("{scope}", scope), json=payload)
            assert response.status_code == 422, response.text
        candidates = transport.post("/v1/artifact-candidates/list", json={"scope_id": scope, "status": "pending"})
        assert candidates.status_code == 200 and candidates.json()["candidates"] == []
        sources = transport.get(f"/v1/scopes/{scope}/sources")
        assert sources.status_code == 200 and "outcome-1" not in sources.text


def _with_scope(value: Any, scope: str) -> Any:
    if isinstance(value, dict):
        return {key: _with_scope(item, scope) for key, item in value.items()}
    if isinstance(value, list):
        return [_with_scope(item, scope) for item in value]
    return scope if value == "{scope}" else value
