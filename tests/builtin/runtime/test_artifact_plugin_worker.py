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

"""Plugin Workers preserve deployment inference identity when opening storage."""

import asyncio
import hashlib
import json

import pytest
import yaml
from sqlalchemy import insert

from powercontext.builtin.artifact_plugin_validation import _PluginManifest, describe_module, load_plugin
from powercontext.builtin.persistence.custom_artifacts import custom_artifact_repository
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.supervision import ArtifactProcessingLeaseRepository
from powercontext.builtin.persistence.tables import (
    ARTIFACT_FAMILY_SCHEMAS_TABLE,
    ARTIFACT_PROCESSING_BINDING_STATES_TABLE,
)
from powercontext.builtin.runtime.artifact_plugins import ArtifactPluginWorkerSpec, run_artifact_plugin_worker
from powercontext.builtin.runtime.composition import open_builtin_contexts
from powercontext.builtin.runtime.config import BuiltinConfig, InferenceConfig, SQLiteConfig
from powercontext.builtin.runtime.processing_contracts import ArtifactProcessingWorkAssignment
from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY, ContentSource
from powercontext.sources import SourceMaterialization
from tests.e2e.test_artifact_plugins import EXAMPLE, deploy_plugin


@pytest.mark.parametrize("configured_inference", [False, True])
def test_plugin_worker_generates_with_deployment_inference(tmp_path, configured_inference):
    directory = str(deploy_plugin(EXAMPLE, tmp_path / "plugin", None))
    manifest = _PluginManifest.model_validate(yaml.safe_load((tmp_path / "plugin/plugin.yml").read_text()))
    definition = describe_module(load_plugin(directory, manifest), manifest, directory)
    definition["directory"] = directory
    declaration = definition["declaration"]
    family, binding = declaration["family"], manifest.binding_name
    # Only storage composition is used: this model is never called by the deterministic plugin.
    inference = InferenceConfig(generation_model="openai:gpt-4o-mini") if configured_inference else None
    config = BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'worker.db'}"),
        inference=inference or InferenceConfig(),
    )

    async def prepare():
        async with open_builtin_contexts(config) as storage, storage.database.transaction() as connection:
            await connection.execute(
                insert(ARTIFACT_FAMILY_SCHEMAS_TABLE).values(
                    family=family,
                    schema_version="1",
                    declaration=declaration,
                )
            )
            await connection.execute(
                insert(ARTIFACT_PROCESSING_BINDING_STATES_TABLE).values(
                    binding_name=binding,
                    binding_generation=1,
                    phase="enabled",
                )
            )
            source = await SourceRepository(BUILTIN_SOURCE_REGISTRY).add(
                connection,
                "scope",
                ContentSource(
                    name="ticket",
                    materialization=SourceMaterialization.CAPTURED,
                    content="Example ticket",
                ),
            )
            lease = await ArtifactProcessingLeaseRepository().start_single_process_term(connection, "test-owner")
            intent = await ArtifactProcessingIntentRepository().request(connection, "scope", binding)
            assignment = ArtifactProcessingWorkAssignment(
                binding_name=binding,
                scope_id="scope",
                artifact_family=family,
                claimed_request_generation=intent.requested_generation,
                fence=lease.fence("single-process"),
                worker_id="test-worker",
            )
            return assignment, source.ref

    assignment, source_ref = asyncio.run(prepare())
    run_artifact_plugin_worker(
        ArtifactPluginWorkerSpec(
            config.database,
            binding,
            1,
            definition,
            inference_config=inference,
            runtime_config=config.runtime,
        ),
        assignment,
    )

    async def verify():
        async with open_builtin_contexts(config) as storage, storage.database.transaction() as connection:
            identity = json.dumps(source_ref.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            artifact_id = hashlib.sha256(identity.encode()).hexdigest()
            report = await custom_artifact_repository(family).latest(connection, "scope", family, artifact_id)
            assert report.content.content["evidence_text"] == "Example ticket"
            assert report.lineage.sources == (source_ref,)
            intent = await ArtifactProcessingIntentRepository().load(connection, "scope", binding)
            assert intent is not None
            assert intent.handled_generation == assignment.claimed_request_generation

    asyncio.run(verify())
