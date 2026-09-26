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

from powercontext.builtin.persistence.processing_migration import ProcessingSchemaNotReadyError
from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig
from powercontext.builtin.runtime.config import InferenceConfig
from powercontext.builtin.runtime.decision_model import (
    DecisionOutcome,
    DecisionRequest,
    DecisionResult,
    FailOpenDecisionModel,
)
from powercontext.builtin.runtime.processing_registry import canonical_processing_manifest, processing_capabilities


class _FailingBackend:
    """A backend whose evaluation fails with the supplied exception."""

    policy_id = "powercontext.decision.failing.v1"

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def evaluate(self, request: DecisionRequest, /) -> DecisionResult:
        raise self._error


def test_decision_configuration_does_not_change_the_processing_manifest() -> None:
    base = BuiltinConfig(inference=InferenceConfig(generation_model="test"))
    decision = BuiltinConfig(
        runtime=RuntimeConfig(decision_assistance_enabled=True),
        inference=InferenceConfig(
            generation_model="test",
            decision_model="openai-chat:decider",
            decision_timeout_seconds=5,
            decision_max_requests=2,
        ),
    )

    assert canonical_processing_manifest(decision) == canonical_processing_manifest(base)


def test_decision_assistance_adds_no_processing_capability() -> None:
    base = BuiltinConfig(inference=InferenceConfig(generation_model="test"))
    decision = BuiltinConfig(
        runtime=RuntimeConfig(decision_assistance_enabled=True),
        inference=InferenceConfig(generation_model="test", decision_model="openai-chat:decider"),
    )

    assert processing_capabilities(decision) == processing_capabilities(base)


def test_decision_backend_failure_is_not_mapped_to_a_schema_error() -> None:
    async def scenario() -> None:
        # A schema-not-ready failure underneath the backend is absorbed into a no-op abstention,
        # never surfaced to the caller as a processing-schema error.
        envelope = FailOpenDecisionModel(_FailingBackend(ProcessingSchemaNotReadyError(reason="backend-down")))

        result = await envelope.evaluate(DecisionRequest("memory.write-gate", "Keep this?", "note"))

        assert result.outcome is DecisionOutcome.ABSTAIN
        assert result.used_fallback is True

    asyncio.run(scenario())
