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

"""Atomic Memory refuses the legacy Memory write gate before any write path starts."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from powercontext.builtin.artifacts.memory import (
    MemoryWriteAssessment,
    MemoryWriteGateRequest,
    MemoryWriteRejectionCode,
    MemoryWriteVerdict,
)
from powercontext.builtin.inference import InferenceUsage
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig, open_builtin_contexts, open_builtin_runtime
from powercontext.builtin.runtime.composition import BuiltinConfigurationError
from powercontext.builtin.runtime.decision_model import DecisionOutcome, DecisionRequest, DecisionResult

_SCRIPTED_POLICY_ID = "test.memory.write-gate.v1"


class _ScriptedGate:
    """A gate that returns one prepared assessment and records its requests."""

    policy_id = _SCRIPTED_POLICY_ID

    def __init__(self, assessment: MemoryWriteAssessment) -> None:
        self._assessment = assessment
        self.requests: list[MemoryWriteGateRequest] = []

    async def assess(self, request: MemoryWriteGateRequest, /) -> MemoryWriteAssessment:
        self.requests.append(request)
        return self._assessment


class _InsufficientDecisionModel:
    """A backend that always answers "evidence is insufficient" (the hold direction)."""

    policy_id = "test.decision.insufficient.v1"

    async def evaluate(self, request: DecisionRequest, /) -> DecisionResult:
        return DecisionResult(DecisionOutcome.YES, self.policy_id, InferenceUsage(requests=1))


def _assessment(
    verdict: MemoryWriteVerdict,
    *,
    code: MemoryWriteRejectionCode | None = None,
    reason: str | None = None,
) -> MemoryWriteAssessment:
    return MemoryWriteAssessment(verdict=verdict, policy_id=_SCRIPTED_POLICY_ID, code=code, reason=reason)


def _config(tmp_path: Path, runtime: RuntimeConfig | None = None, database: str = "gate.db") -> BuiltinConfig:
    return BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / database}"),
        runtime=RuntimeConfig() if runtime is None else runtime,
    )


def test_config_rejects_the_legacy_gate_before_the_decision_backend(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = _config(tmp_path, RuntimeConfig(memory_write_gate_enabled=True), database="enabled.db")
        with pytest.raises(BuiltinConfigurationError, match=r"Atomic Memory.*legacy Memory write gate"):
            async with open_builtin_runtime(config, decision_model=_InsufficientDecisionModel()):
                pytest.fail("Legacy gate configuration must fail before Runtime startup")

    asyncio.run(scenario())


def test_config_rejects_the_legacy_gate_without_a_backend(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = _config(tmp_path, RuntimeConfig(memory_write_gate_enabled=True), database="unavailable.db")
        with pytest.raises(BuiltinConfigurationError, match=r"Atomic Memory.*legacy Memory write gate"):
            async with open_builtin_contexts(config):
                pytest.fail("Legacy gate configuration must fail before Contexts startup")

    asyncio.run(scenario())


def test_runtime_rejects_an_injected_legacy_gate_before_an_explicit_write(tmp_path: Path) -> None:
    async def scenario() -> None:
        gate = _ScriptedGate(
            _assessment(
                MemoryWriteVerdict.HOLD,
                code=MemoryWriteRejectionCode.INSUFFICIENT_COVERAGE,
                reason="the citation is thin",
            )
        )
        with pytest.raises(BuiltinConfigurationError, match=r"Atomic Memory.*legacy Memory write gate"):
            async with open_builtin_runtime(_config(tmp_path), memory_write_gate=gate):
                pytest.fail("Injected legacy gates must fail before Runtime startup")
        assert gate.requests == []

    asyncio.run(scenario())


def test_contexts_reject_an_injected_legacy_gate_before_ingestion(tmp_path: Path) -> None:
    async def scenario() -> None:
        gate = _ScriptedGate(
            _assessment(
                MemoryWriteVerdict.HOLD,
                code=MemoryWriteRejectionCode.INSUFFICIENT_COVERAGE,
                reason="the window evidence is thin",
            )
        )
        with pytest.raises(BuiltinConfigurationError, match=r"Atomic Memory.*legacy Memory write gate"):
            async with open_builtin_contexts(_config(tmp_path), memory_write_gate=gate):
                pytest.fail("Injected legacy gates must fail before Contexts startup")
        assert gate.requests == []

    asyncio.run(scenario())
