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
from typing import Any

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from powercontext.builtin.artifacts.atomic_memory.reconciliation import (
    AtomicMemoryComparisonItem,
    AtomicMemoryReconciliationInput,
    AtomicMemoryReconciliationOutput,
)
from powercontext.builtin.inference.pydantic_ai import PydanticAIStructuredGenerator


def test_reconciliation_generator_advertises_only_model_owned_content() -> None:
    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        output = info.model_request_parameters.output_object
        assert output is not None
        schema = output.json_schema
        (content_schema,) = tuple(item for item in schema["properties"]["content"]["anyOf"] if "$ref" in item)
        content = schema["$defs"][content_schema["$ref"].rsplit("/", 1)[-1]]
        assert set(content["properties"]) == {"kind", "text"}
        assert content["additionalProperties"] is False
        assert "AtomicMemoryCreation" not in schema.get("$defs", {})
        return ModelResponse(parts=[TextPart('{"action":"noop","compared_ids":[],"reason":"Unsupported."}')])

    async def scenario() -> None:
        generator = PydanticAIStructuredGenerator(
            model=FunctionModel(respond),
            instructions="Reconcile only the supplied items and evidence.",
            input_type=AtomicMemoryReconciliationInput,
            output_type=AtomicMemoryReconciliationOutput,
        )
        await generator.generate(
            AtomicMemoryReconciliationInput(
                proposal=AtomicMemoryComparisonItem(
                    item_id="candidate:1", kind="fact", text="Unverified.", original_refs=(), evidence_ids=()
                ),
                related=(),
                evidence=(),
            )
        )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "metadata",
    [
        {"creation": {"type": "merge", "input_artifact_ids": ["memory-a", "memory-b"]}},
        {"schema": "unsupported"},
        {"schema_": "unsupported"},
        {"revision": 1},
    ],
)
def test_reconciliation_output_rejects_externally_supplied_metadata(metadata: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        AtomicMemoryReconciliationOutput.model_validate({
            "action": "create",
            "compared_ids": [],
            "content": {"kind": "fact", "text": "Supplied fact.", **metadata},
            "evidence_ids": ["source:1"],
            "reason": "Supported by the Source.",
        })


@pytest.mark.parametrize("mode", ["validation", "serialization"])
def test_reconciliation_output_json_schema_omits_service_owned_metadata(mode) -> None:
    schema = AtomicMemoryReconciliationOutput.model_json_schema(mode=mode)
    (content_schema,) = tuple(item for item in schema["properties"]["content"]["anyOf"] if "$ref" in item)
    content = schema["$defs"][content_schema["$ref"].rsplit("/", 1)[-1]]
    assert set(content["properties"]) == {"kind", "text"}
