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

import asyncio
from contextlib import AsyncExitStack
from pathlib import Path

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    MemoryEntryInput,
    RuntimeConfig,
    open_builtin_contexts,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.composition import _generation_pipelines
from powercontext.builtin.runtime.config import InferenceConfig
from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY


def _config(tmp_path: Path) -> BuiltinConfig:
    return BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'default-off.db'}"),
        runtime=RuntimeConfig(decision_assistance_enabled=False),
    )


def test_decision_role_is_absent_when_disabled(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(_config(tmp_path)) as runtime:
            assert runtime.decision_model is None

    asyncio.run(scenario())


def test_disabled_decision_builds_no_backend_and_no_readiness_probe() -> None:
    async def scenario() -> None:
        async with AsyncExitStack() as resources:
            pipelines = await _generation_pipelines(
                InferenceConfig(),
                RuntimeConfig(decision_assistance_enabled=False),
                resources,
                None,
                BUILTIN_SOURCE_REGISTRY,
            )

        assert pipelines[7] is None
        assert pipelines[10] is None

    asyncio.run(scenario())


def test_disabled_decision_leaves_the_ordinary_memory_path_unchanged(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(_config(tmp_path)) as contexts:
            context = await contexts.get("project")
            stored = await context.artifacts.memory.remember(
                memory=None,
                entries=(MemoryEntryInput(kind="decision", text="Baseline memory."),),
                mode="append",
            )
            assert stored is not None
            result = await context.artifacts.memory.search("baseline", memories=(stored,), mode="fts")

            assert [hit.text for hit in result.hits] == ["Baseline memory."]

    asyncio.run(scenario())
