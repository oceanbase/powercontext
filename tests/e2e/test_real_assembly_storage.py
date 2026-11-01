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

"""Assembly fixtures must prepare their final database before real Server startup."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.embeddings import TestEmbeddingModel

from powercontext.builtin.runtime import InferenceConfig, RuntimeConfig
from powercontext.client import PowerContextClient
from powercontext.http import CreateScopeRequest, MemorySearchMode, RememberMemoryRequest, SearchMemoryRequest
from powercontext.server.settings import ServerSettings
from tests.e2e.atomic_memory_migration_backend import MigrationBackend
from tests.e2e.real_experience_skill import test_context_text_assembly, test_profile_context_assembly


class _ReachedAssemblyScenario(Exception):
    """Stop after proving real storage readiness, before native host/model scenarios."""


@pytest.mark.parametrize("migration_backend", ("oceanbase",), indirect=True)
@pytest.mark.parametrize("assembly", ("text", "profile"))
def test_assembly_fixture_initializes_its_replacement_database(
    migration_backend: MigrationBackend,
    assembly: str,
    tmp_path: Path,
    pytestconfig: pytest.Config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = ServerSettings(
        database=migration_backend.config,
        inference=InferenceConfig(
            generation_model="test",
            embedding_model="test",
            embedding_profile_id="assembly-fixture",
            embedding_dimension=3,
        ),
        runtime=RuntimeConfig(artifact_processing_role="api"),
    )
    module = test_context_text_assembly if assembly == "text" else test_profile_context_assembly
    monkeypatch.setattr(module, "ServerSettings", lambda: settings)
    monkeypatch.setattr(module, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "pydantic_ai.embeddings.infer_embedding_model", lambda *args, **kwargs: TestEmbeddingModel(dimensions=3)
    )

    async def verify_storage(url, token, scope_ids, *_args):
        async with PowerContextClient(url, token=token, timeout=60) as client:
            scope = await client.create_scope(
                CreateScopeRequest(
                    title="Assembly storage regression",
                    summary="The actual replacement database supports configured vectors",
                    idempotency_key="assembly-storage-regression",
                )
            )
            scope_ids.append(scope.scope_id)
            written = await client.remember_memory(
                RememberMemoryRequest(scope_id=scope.scope_id, kind="fact", text="Verify the release gate.")
            )
            assert len(written.records) == 1
            found = await client.search_memory(
                SearchMemoryRequest(scope_id=scope.scope_id, query="release gate", mode=MemorySearchMode.VECTOR)
            )
            assert len(found.hits) == 1
            assert found.hits[0].memory.artifact == written.records[0].artifact
            assert "vector" in found.hits[0].matched_by
        raise _ReachedAssemblyScenario

    if assembly == "text":
        monkeypatch.setattr(module, "_api_scenario", verify_storage)
        entrypoint = test_context_text_assembly.test_configured_services_and_native_codex_consume_standard_text
    else:
        monkeypatch.setattr(module, "_scenario", verify_storage)
        entrypoint = test_profile_context_assembly.test_configured_profile_generation_review_and_context_delivery
    with pytest.raises(_ReachedAssemblyScenario):
        entrypoint(tmp_path, pytestconfig)
