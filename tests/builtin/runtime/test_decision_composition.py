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
from typing import Any

import pytest

from powercontext.builtin.inference import InferenceUnavailableError, InferenceUsage
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    RuntimeConfig,
    open_builtin_runtime,
    preflight_builtin_runtime,
)
from powercontext.builtin.runtime.composition import BuiltinConfigurationError, _generation_pipelines
from powercontext.builtin.runtime.config import InferenceConfig
from powercontext.builtin.runtime.decision_model import (
    DECISION_INSTRUCTIONS_VERSION,
    DecisionOutcome,
    DecisionRequest,
    DecisionResult,
    FailOpenDecisionModel,
    LLMDecisionModel,
)
from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY


class _FakeDecisionModel:
    """Injected backend used to prove the seam, wrapping, and fail-open path."""

    policy_id = "powercontext.decision.fake.v1"

    def __init__(self, *, outcome: DecisionOutcome = DecisionOutcome.YES, fail: bool = False) -> None:
        self._outcome = outcome
        self._fail = fail
        self.requests: list[DecisionRequest] = []

    async def evaluate(self, request: DecisionRequest, /) -> DecisionResult:
        self.requests.append(request)
        if self._fail:
            raise InferenceUnavailableError("evaluate")
        return DecisionResult(self._outcome, self.policy_id, InferenceUsage(requests=1))


def _config(tmp_path: Path, **runtime: Any) -> BuiltinConfig:
    return BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
        runtime=RuntimeConfig(**runtime),
    )


def test_decision_role_is_disabled_without_an_opt_in(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(_config(tmp_path)) as runtime:
            assert runtime.decision_model is None

    asyncio.run(scenario())


def test_injected_decision_model_is_exposed_fail_open_wrapped(tmp_path: Path) -> None:
    async def scenario() -> None:
        delegate = _FakeDecisionModel()
        async with open_builtin_runtime(_config(tmp_path), decision_model=delegate) as runtime:
            exposed = runtime.decision_model
            assert isinstance(exposed, FailOpenDecisionModel)
            assert exposed.policy_id == delegate.policy_id

            request = DecisionRequest("memory.write-gate", "Keep this?", "note")
            result = await exposed.evaluate(request)

            assert result.outcome is DecisionOutcome.YES
            assert result.used_fallback is False
            assert delegate.requests == [request]

    asyncio.run(scenario())


def test_injected_decision_failure_degrades_on_the_exposed_seam(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(_config(tmp_path), decision_model=_FakeDecisionModel(fail=True)) as runtime:
            exposed = runtime.decision_model
            assert exposed is not None

            result = await exposed.evaluate(DecisionRequest("memory.write-gate", "Keep this?", "note"))

            assert result.outcome is DecisionOutcome.ABSTAIN
            assert result.used_fallback is True

    asyncio.run(scenario())


def test_preflight_rejects_an_enabled_decision_role_without_a_model() -> None:
    config = BuiltinConfig(runtime=RuntimeConfig(decision_assistance_enabled=True))

    async def scenario() -> None:
        with pytest.raises(BuiltinConfigurationError):
            await preflight_builtin_runtime(config)

    asyncio.run(scenario())


def test_preflight_accepts_an_enabled_decision_role_with_a_dedicated_model(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    config = BuiltinConfig(
        runtime=RuntimeConfig(decision_assistance_enabled=True),
        inference=InferenceConfig(decision_model="openai-chat:decision-model"),
    )

    async def scenario() -> None:
        await preflight_builtin_runtime(config)

    asyncio.run(scenario())


def test_generation_pipelines_builds_the_decision_backend_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    async def scenario() -> None:
        async with AsyncExitStack() as resources:
            pipelines = await _generation_pipelines(
                InferenceConfig(decision_model="openai-chat:decision-model"),
                RuntimeConfig(decision_assistance_enabled=True),
                resources,
                None,
                BUILTIN_SOURCE_REGISTRY,
            )

        decision = pipelines[7]
        assert isinstance(decision, LLMDecisionModel)
        assert decision.policy_id == DECISION_INSTRUCTIONS_VERSION
        # A dedicated decision model also yields a non-blocking readiness probe.
        assert pipelines[10] is not None

    asyncio.run(scenario())


def test_decision_role_is_not_registered_as_an_mcp_tool() -> None:
    from powercontext.server import mcp

    assert all("decision" not in operation_id for operation_id in mcp._MCP_OPERATION_IDS)
