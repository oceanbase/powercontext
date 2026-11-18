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

"""Invoke a plugin through the real executor with a single-process Supervisor lease."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

from powercontext.artifacts.search import ArtifactSearchExecutionContext
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.supervision import ArtifactProcessingLeaseRepository
from powercontext.builtin.plugins.executor import PluginWorkerSpec, execute_plugin
from powercontext.builtin.plugins.families import plugin_artifact_repository
from powercontext.builtin.plugins.manifest import load_plugin
from powercontext.builtin.plugins.writer import PluginArtifactWriter, UserChange, plugin_merge_service
from powercontext.builtin.runtime.artifact_merge import ArtifactMergeAccess, ArtifactMergeApplication
from powercontext.builtin.runtime.config import SQLiteConfig
from powercontext.builtin.runtime.processing_contracts import ArtifactProcessingWorkAssignment
from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY

DECLARATION = """

from powercontext.plugins import ArtifactPlugin as _ArtifactPlugin, Inputs as _Inputs

plugin = _ArtifactPlugin(
    family={family!r},
    inputs=_Inputs(source_types=("content",), projection="powercontext.text-evidence@1"),
    setup=setup,
    **{spec!r},
)
"""


def write_plugin(root: Path, name: str, code: str, **spec: Any) -> Path:
    """Write ``code`` as ``plugin.py`` and declare it as a plugin consuming content Sources."""

    directory = root / name
    directory.mkdir(parents=True)
    declaration = DECLARATION.format(family=name, spec={"on_sources": True, **spec})
    (directory / "plugin.py").write_text(textwrap.dedent(code) + declaration, encoding="utf-8")
    return directory


def _family(manifest) -> str:
    assert manifest.artifact is not None
    return manifest.artifact.family


async def invoke(database, directory: Path, scope_id: str):
    manifest, _ = load_plugin(directory)
    async with database.transaction() as connection:
        lease = await ArtifactProcessingLeaseRepository().start_single_process_term(connection, "test-owner")
        intent = await ArtifactProcessingIntentRepository().request(connection, scope_id, manifest.binding_name)
    assignment = ArtifactProcessingWorkAssignment(
        binding_name=manifest.binding_name,
        scope_id=scope_id,
        artifact_family=_family(manifest),
        claimed_request_generation=intent.requested_generation,
        fence=lease.fence("single-process"),
        worker_id="test-worker",
    )
    # The executor uses the caller's open database instead of the configuration.
    spec = PluginWorkerSpec(
        database=SQLiteConfig(),
        directory=str(directory),
        manifest=manifest.model_dump(mode="json"),
        storage_root=str(directory.parent.parent / "plugin-storage"),
    )
    return await execute_plugin(spec, assignment, database=database)


async def edit(database, directory: Path, scope_id: str, artifact_id: str, content, *, expected_revision: int):
    """Replace a plugin Artifact the way the host's ``update_artifact`` writer does."""

    manifest, _ = load_plugin(directory)
    writer = PluginArtifactWriter(
        database=database,
        scope_id=scope_id,
        family=_family(manifest),
        sources=SourceRepository(BUILTIN_SOURCE_REGISTRY),
        change=UserChange("update", artifact_id),
    )
    async with writer.transaction() as tx:
        return await tx.put(artifact_id, content=content, expected_revision=expected_revision)


def governance(database, directory: Path, scope_id: str):
    """The merge, forget and restoration the Server's generic routes run for a plugin family."""

    manifest, _ = load_plugin(directory)
    family = _family(manifest)
    service = plugin_merge_service(family, ArtifactMergeAccess(family), SourceRepository(BUILTIN_SOURCE_REGISTRY))
    application = ArtifactMergeApplication(
        database, (service,), default_context=ArtifactSearchExecutionContext(trusted_local=True)
    )
    return application.for_scope(scope_id, family)


async def current(database, family: str, scope_id: str, artifact_id: str):
    async with database.transaction() as connection:
        return await plugin_artifact_repository(family).latest(connection, scope_id, family, artifact_id)
