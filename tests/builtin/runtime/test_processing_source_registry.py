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

"""Reject Source capabilities that built-in child processes cannot reconstruct."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest

from powercontext import AdapterSourceDefinition, SourceDefinitionRegistry
from powercontext.builtin.inference import character_token_estimator
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, InferenceConfig, RuntimeConfig, composition
from powercontext.builtin.runtime.artifact_processing import ArtifactProcessingBinding
from powercontext.builtin.runtime.composition import BuiltinConfigurationError, open_builtin_runtime
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY, CONTENT_SOURCE_DEFINITION
from tests.builtin.persistence.test_provider import CustomCapture, CustomSource, CustomSourceAdapter
from tests.builtin.runtime.test_processing_composition import _OCEANBASE

_FAMILIES = ("memory", "topic-memory", "experience", "profile")


def _custom_registry():
    return SourceDefinitionRegistry((
        *BUILTIN_SOURCE_REGISTRY.definitions,
        AdapterSourceDefinition(CustomSourceAdapter()),
    ))


@pytest.mark.parametrize("family", _FAMILIES)
@pytest.mark.parametrize("mode", ["global", "dedicated"])
def test_runtime_rejects_custom_sources_before_bootstrap_and_allows_corrected_restart(
    tmp_path, monkeypatch, family, mode
):
    async def scenario():
        launcher = Mock(side_effect=AssertionError("unsupported Source registry reached a child launcher"))
        monkeypatch.setattr(composition, "SpawnArtifactProcessingWorkerLauncher", launcher)
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'unsupported.db'}"),
            inference=InferenceConfig(generation_model="test"),
            runtime=RuntimeConfig(artifact_processing_families=(family,), artifact_processing_supervisor_mode=mode),
        )
        # Schedules are disabled, but explicit requests would still spawn children.
        with pytest.raises(BuiltinConfigurationError, match="built-in Source definitions"):
            async with open_builtin_runtime(
                config, source_registry=_custom_registry(), scheduler_path=tmp_path / "scheduler.db"
            ):
                pytest.fail("unsupported child Source capabilities must fail before accepting work")
        launcher.assert_not_called()
        # A configuration rejection must not freeze a completed deployment
        # manifest that prevents the suggested correction on the same database.
        assert not (tmp_path / "unsupported.db").exists()
        corrected = config.model_copy(
            update={"runtime": RuntimeConfig(artifact_processing_families=(), artifact_processing_supervisor_mode=mode)}
        )
        async with open_builtin_runtime(
            corrected, source_registry=_custom_registry(), scheduler_path=tmp_path / "scheduler.db"
        ) as runtime:
            context = await runtime._provider.get("project")
            resolved = await context.sources.resolve(CustomCapture(source_id="custom", value="corrected startup"))
            stored = await context.sources.add(resolved)
            assert isinstance(stored, CustomSource)
            assert await context.sources.read(stored) == "corrected startup"
            assert runtime.artifact_processing_supervisor is None
        launcher.assert_not_called()

    asyncio.run(scenario())


def test_runtime_preserves_custom_source_persistence_with_builtin_families_disabled(tmp_path):
    async def scenario():
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'synchronous.db'}"),
            inference=InferenceConfig(generation_model="test"),
            runtime=RuntimeConfig(artifact_processing_families=()),
        )
        registry = _custom_registry()
        async with open_builtin_runtime(
            config, source_registry=registry, scheduler_path=tmp_path / "scheduler.db"
        ) as runtime:
            context = await runtime._provider.get("project")
            resolved = await context.sources.resolve(CustomCapture(source_id="custom", value="retained typed value"))
            stored = await context.sources.add(resolved)
            assert isinstance(stored, CustomSource)
            assert await context.sources.read(stored) == "retained typed value"
            assert await context.sources.list() == (stored,)
            assert runtime.artifact_processing_supervisor is None

    asyncio.run(scenario())


@pytest.mark.parametrize("role", ["all", "background"])
def test_custom_bindings_must_cover_every_enabled_family_with_custom_sources(role):
    config = BuiltinConfig(
        database=_OCEANBASE,
        inference=InferenceConfig(generation_model="test"),
        runtime=RuntimeConfig(artifact_processing_role=role, artifact_processing_families=_FAMILIES),
    )
    bindings = tuple(
        ArtifactProcessingBinding(binding_name=f"custom-{family}", artifact_family=family, launcher=Mock())
        for family in _FAMILIES
    )
    contexts = cast(RelationalContexts, SimpleNamespace())
    assert (
        composition._artifact_processing_bindings(config, contexts, bindings, source_registry=_custom_registry())
        == bindings
    )
    with pytest.raises(BuiltinConfigurationError, match="built-in Source definitions"):
        composition._artifact_processing_bindings(config, contexts, bindings[:-1], source_registry=_custom_registry())


def test_api_role_preserves_custom_registry_without_creating_builtin_children():
    config = BuiltinConfig(
        database=_OCEANBASE,
        runtime=RuntimeConfig(artifact_processing_role="api", artifact_processing_families=_FAMILIES),
    )
    assert (
        composition._artifact_processing_bindings(
            config, cast(RelationalContexts, SimpleNamespace()), (), source_registry=_custom_registry()
        )
        == ()
    )


def test_copied_builtin_registry_is_accepted_but_replaced_definitions_are_rejected(tmp_path):
    config = BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'builtin.db'}"),
        inference=InferenceConfig(generation_model="test"),
        runtime=RuntimeConfig(artifact_processing_families=("memory",)),
    )
    contexts = cast(RelationalContexts, SimpleNamespace(database=Mock(), token_estimator=character_token_estimator()))
    copied = SourceDefinitionRegistry(BUILTIN_SOURCE_REGISTRY.definitions)
    assert len(composition._artifact_processing_bindings(config, contexts, (), source_registry=copied)) == 1

    definitions = BUILTIN_SOURCE_REGISTRY.definitions
    # Identical name/version does not guarantee identical adapter/projection behavior.
    changed = SourceDefinitionRegistry((replace(CONTENT_SOURCE_DEFINITION, projections=()), *definitions[1:]))
    with pytest.raises(BuiltinConfigurationError, match="built-in Source definitions"):
        composition._artifact_processing_bindings(config, contexts, (), source_registry=changed)
