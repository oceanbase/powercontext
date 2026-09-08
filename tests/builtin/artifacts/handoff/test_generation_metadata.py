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

import pytest

from powercontext.builtin.artifacts.handoff import HandoffDraft, HandoffSourceCitation, HandoffStatement
from powercontext.builtin.artifacts.handoff.generation_metadata import (
    HandoffGenerationEnvelope,
    HandoffGenerationReceipts,
)
from powercontext.builtin.artifacts.prompt import Prompt, PromptContent, PromptError
from powercontext.builtin.artifacts.prompt.builtin import builtin_prompt_definitions
from powercontext.sources import SourceRef


def _draft() -> HandoffDraft:
    return HandoffDraft(
        objective="Continue testing.",
        state=(
            HandoffStatement(
                text="The test passed.",
                citations=(HandoffSourceCitation(source_ref=SourceRef(source_type="content", source_id="test")),),
            ),
        ),
        disposition="complete",
    )


def test_receipt_retains_exact_origin_across_edits_and_key_rotation() -> None:
    prompt = Prompt(
        artifact_id="handoff.generate",
        revision=2,
        content=PromptContent(
            schema_version="powercontext.prompt.v1",
            mode="custom",
            instructions="Keep verified results.",
            demonstrations=(),
        ),
    )
    definition = next(item for item in builtin_prompt_definitions() if item.key == "handoff.generate")
    selection = definition.resolve("scope", prompt)
    original = HandoffGenerationReceipts(b"original-key-for-test".ljust(32, b"x"))
    draft = _draft()
    receipt = original.issue("scope", selection, draft)
    metadata = original.verify("scope", receipt, draft.as_content())
    assert metadata.edit_status == "unchanged"
    assert metadata.artifact == prompt.as_ref()
    edited = draft.model_copy(update={"objective": "Continue with a corrected objective."})
    rotated = HandoffGenerationReceipts(
        b"new-key-for-test".ljust(32, b"x"),
        verification_keys=(b"original-key-for-test".ljust(32, b"x"),),
    )
    assert rotated.verify("scope", receipt, draft.as_content()) == metadata
    assert rotated.verify("scope", receipt, edited.as_content()).edit_status == "edited"
    assert "receipt" not in metadata.model_dump_json()
    assert "Keep verified results." not in receipt.model_dump_json()
    with pytest.raises(PromptError) as pruned:
        HandoffGenerationReceipts(b"new-key-for-test".ljust(32, b"x")).verify("scope", receipt, draft)
    assert pruned.value.code == "invalid_handoff_generation"
    with pytest.raises(PromptError):
        rotated.verify("other-scope", receipt, draft)
    for tampered in ("invalid", receipt.receipt + "x", "x" + receipt.receipt, receipt.receipt.replace(".", "..")):
        with pytest.raises(PromptError):
            rotated.verify("scope", HandoffGenerationEnvelope(receipt=tampered), draft)


def test_absent_generation_preserves_legacy_handoff_json() -> None:
    draft = _draft()
    assert "generation" not in draft.model_dump(mode="json")
    assert "generation" not in draft.as_content().model_dump(mode="json")
