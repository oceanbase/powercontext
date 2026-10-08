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
from pathlib import Path

from powercontext.builtin.inference import InferenceConfigurationError, InferenceUnavailableError
from powercontext.builtin.inference.pydantic_ai import PydanticAIConfigurationError
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    InferenceConfig,
    ReadinessCheckStatus,
    RuntimeConfig,
    RuntimeReadinessStatus,
    dependency_readiness_probe,
    open_builtin_runtime,
)


def test_builtin_runtime_reports_runtime_and_database_readiness(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
        )
        async with open_builtin_runtime(config) as runtime:
            readiness = await runtime.readiness()
            supervisor = runtime.artifact_processing_supervisor
            await runtime.close()

        assert readiness.status is RuntimeReadinessStatus.READY
        assert readiness.checks == {
            "runtime": ReadinessCheckStatus.READY,
            "database": ReadinessCheckStatus.READY,
            "artifact_processing_supervisor": "disabled",
        }
        assert supervisor is None

    asyncio.run(scenario())


def test_dependency_readiness_probe_surfaces_a_stable_redacted_configuration_reason() -> None:
    async def reject() -> None:
        raise PydanticAIConfigurationError("provider-rejected", detail="HTTP 400")

    async def scenario() -> None:
        probe = dependency_readiness_probe(reject)

        assert await probe() == "misconfigured: provider-rejected (HTTP 400)"

    asyncio.run(scenario())


def test_dependency_readiness_probe_redacts_plain_configuration_errors() -> None:
    async def reject() -> None:
        raise InferenceConfigurationError("secret provider response")  # noqa: TRY003 - verifies redaction

    async def scenario() -> None:
        probe = dependency_readiness_probe(reject)

        assert await probe() == "misconfigured"

    asyncio.run(scenario())


def test_inference_decision_readiness_is_registered_and_non_blocking(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    async def unavailable(*args, **kwargs) -> None:
        raise InferenceUnavailableError("evaluate")

    monkeypatch.setattr("powercontext.builtin.inference.pydantic_ai.probe_pydantic_ai_model", unavailable)

    async def scenario() -> None:
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'decision-readiness.db'}"),
            runtime=RuntimeConfig(decision_assistance_enabled=True),
            inference=InferenceConfig(decision_model="openai-chat:decision-model"),
        )
        async with open_builtin_runtime(config) as runtime:
            readiness = await runtime.readiness()
            await runtime.close()

        # An enabled decision model registers a non-blocking probe: its failure degrades
        # readiness without turning the Runtime NOT_READY.
        assert readiness.checks["inference.decision"] is ReadinessCheckStatus.UNAVAILABLE
        assert readiness.status is RuntimeReadinessStatus.DEGRADED

    asyncio.run(scenario())
