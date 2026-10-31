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

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryEvidence
from powercontext.builtin.artifacts.atomic_memory.models import (
    AtomicMemoryContent,
    AtomicMemoryRead,
    AtomicMemoryStateValue,
)
from powercontext.builtin.artifacts.atomic_memory.reconciliation import (
    AtomicMemoryComparisonItem,
    AtomicMemoryReconciliationContent,
    AtomicMemoryReconciliationInput,
    AtomicMemoryReconciliationOutput,
    AtomicMemoryWindowWorkset,
    AtomicMemoryWorkingItem,
    validate_reconciliation_decision,
)
from powercontext.builtin.inference import InvalidInferenceOutputError
from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIStructuredGenerator
from powercontext.sources import SourceRef


@pytest.mark.parametrize("action", ["revise", "merge"])
def test_equal_content_can_still_consolidate_published_identities(action) -> None:
    content = AtomicMemoryContent(kind="fact", text="The project uses OceanBase.", description="Project database")
    evidence = AtomicMemoryEvidence(
        evidence_id="source:1",
        source_ref=SourceRef(source_type="content", source_id="session-1"),
        journal_position=1,
        content=content.text,
        source_metadata={},
    )
    published = tuple(
        AtomicMemoryRead(
            ref=ArtifactRef(family="atomic-memory", artifact_id=identity, revision=1),
            state=AtomicMemoryStateValue.ACTIVE,
            state_version=0,
        )
        for identity in ("memory-a", "memory-b")
    )
    workset = AtomicMemoryWindowWorkset()
    workset.add(AtomicMemoryWorkingItem(key="proposal", content=content, origins=published[:1], evidence=(evidence,)))
    workset.add(
        AtomicMemoryWorkingItem(
            key="target", content=content, origins=published[1:] if action == "merge" else (), evidence=(evidence,)
        )
    )
    related = workset.related("proposal", ("target",))
    workset.apply(
        "proposal",
        related,
        AtomicMemoryReconciliationOutput(
            action=action,
            compared_ids=("target",),
            target_ids=("target",),
            content=AtomicMemoryReconciliationContent(
                kind=content.kind, text=content.text, description="Project database"
            ),
            evidence_ids=(evidence.evidence_id,),
            reason="Keep the published identities while consolidating the same fact.",
        ),
    )
    (change,) = workset.changes()
    assert change.content == content
    assert change.origins == (published if action == "merge" else published[:1])


@pytest.mark.parametrize("corrected", [True, False])
def test_unpublished_reconciliation_receives_bounded_identity_correction(corrected: bool) -> None:
    async def scenario() -> None:
        workset = AtomicMemoryWindowWorkset()
        evidence = AtomicMemoryEvidence(
            evidence_id="source:1",
            source_ref=SourceRef(source_type="content", source_id="session-1"),
            journal_position=1,
            content="The project selected OceanBase for persistence.",
            source_metadata={},
        )
        for key, text in (
            ("candidate:0", "The project selected OceanBase."),
            ("candidate:1", "OceanBase supplies persistence."),
        ):
            workset.add(
                AtomicMemoryWorkingItem(
                    key=key, content=AtomicMemoryContent(kind="fact", text=text), evidence=(evidence,), changed=True
                )
            )
        related = workset.related("candidate:1", ("candidate:0",))
        request = workset.request("candidate:1", related)
        attempts = 0

        async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            nonlocal attempts
            attempts += 1
            action = "merge" if corrected and attempts > 1 else "revise"
            output = AtomicMemoryReconciliationOutput(
                action=action,
                compared_ids=("candidate:0",),
                target_ids=("candidate:0",),
                content=AtomicMemoryReconciliationContent(
                    kind="fact",
                    text="The project selected OceanBase for persistence.",
                    description="OceanBase selected for persistence",
                ),
                evidence_ids=("source:1",),
                reason="Combine the two supported statements.",
            )
            return ModelResponse(parts=[TextPart(output.model_dump_json())])

        generator = PydanticAIStructuredGenerator(
            model=FunctionModel(respond),
            instructions="Reconcile the supplied items without allocating published identities.",
            input_type=AtomicMemoryReconciliationInput,
            output_type=AtomicMemoryReconciliationOutput,
            limits=InferenceLimits(max_requests=2),
            output_check=validate_reconciliation_decision,
        )
        if corrected:
            result = await generator.generate(request)
            assert result.output.action == "merge"
            assert result.usage.requests == 2
            key = workset.apply("candidate:1", related, result.output)
            assert workset.items[key].origins == ()
            assert workset.items[key].content.text == "The project selected OceanBase for persistence."
            assert workset.items[key].content.description == "OceanBase selected for persistence"
        else:
            with pytest.raises(InvalidInferenceOutputError):
                await generator.generate(request)
            assert attempts == 2
            assert len(workset.items) == 2

    asyncio.run(scenario())


def test_reconciliation_generator_advertises_only_model_owned_content() -> None:
    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        output = info.model_request_parameters.output_object
        assert output is not None
        schema = output.json_schema
        (content_schema,) = tuple(item for item in schema["properties"]["content"]["anyOf"] if "$ref" in item)
        content = schema["$defs"][content_schema["$ref"].rsplit("/", 1)[-1]]
        assert set(content["properties"]) == {"kind", "text", "description"}
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
            "content": {"kind": "fact", "text": "Supplied fact.", "description": "Supplied fact", **metadata},
            "evidence_ids": ["source:1"],
            "reason": "Supported by the Source.",
        })


@pytest.mark.parametrize("mode", ["validation", "serialization"])
def test_reconciliation_output_json_schema_omits_service_owned_metadata(mode) -> None:
    schema = AtomicMemoryReconciliationOutput.model_json_schema(mode=mode)
    (content_schema,) = tuple(item for item in schema["properties"]["content"]["anyOf"] if "$ref" in item)
    content = schema["$defs"][content_schema["$ref"].rsplit("/", 1)[-1]]
    assert set(content["properties"]) == {"kind", "text", "description"}
