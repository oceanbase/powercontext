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

from types import SimpleNamespace

from fastapi.testclient import TestClient

from powercontext.artifacts.search import ArtifactSearchMatch
from powercontext.builtin.artifacts.atomic_memory import AtomicMemory, AtomicMemoryContent
from powercontext.server.app import create_app


def test_unified_http_preserves_atomic_memory_content_json_alias() -> None:
    artifact = AtomicMemory(
        artifact_id="memory-a",
        revision=2,
        content=AtomicMemoryContent(kind="fact", text="Release rollback uses a reviewed plan."),
    )

    class Artifacts:
        def for_scope(self, scope_id):
            assert scope_id == "scope-a"
            return self

        async def search(self, family, payload, *, execution_context=None):
            assert family == "atomic-memory"
            return SimpleNamespace(matches=(ArtifactSearchMatch(artifact.as_ref(), 1.0),), artifacts=(artifact,))

    with TestClient(create_app(application=SimpleNamespace(artifacts=Artifacts()))) as client:
        response = client.post("/v1/scopes/scope-a/artifacts/atomic-memory/search", json={"query": "rollback"})

    assert response.status_code == 200
    assert response.json()["results"][0]["content"] == {
        "schema": "powercontext.atomic-memory.v1",
        "kind": "fact",
        "text": "Release rollback uses a reviewed plan.",
        "creation": None,
    }
