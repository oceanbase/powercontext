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

"""Temporal evidence preserves precision without treating arbitrary metadata as fact time."""

import json

import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryEvidence
from powercontext.builtin.artifacts.atomic_memory.models import (
    AtomicMemoryContent,
    AtomicMemoryRead,
    AtomicMemoryStateValue,
)
from powercontext.builtin.artifacts.atomic_memory.reconciliation import (
    AtomicMemoryArtifactEvidence,
    AtomicMemoryComparisonItem,
    AtomicMemoryReconciliationContent,
    AtomicMemoryReconciliationInput,
    AtomicMemoryReconciliationOutput,
    validate_reconciliation_decision,
)
from powercontext.builtin.artifacts.atomic_memory.temporal import (
    preserves_time_literals,
    source_time_literals,
    text_time_literals,
)
from powercontext.builtin.inference import InvalidInferenceOutputError
from powercontext.sources import SourceRef


@pytest.mark.parametrize(
    ("provided", "retained", "accepted"),
    [
        ("2026-05-01T00:00:00Z", "2026-05-01", True),
        ("2026-05-01T09:00:00Z", "2026-05-01", False),
        ("2026-05-01T09:00:00+08:00", "2026-05-01T01:00:00Z", True),
        ("2026-05-01T09:00:00+08:00", "2026-05-01T09:00:00Z", False),
        ("2026-05-01T09:00:00", "2026-05-01T09:00:00Z", False),
        ("2026-05-01T09:00:00.123Z", "2026-05-01T09:00:00Z", False),
        ("2026-05-01", "2026-05-010", False),
        ("2026-05-01T09:00:00+0800", "2026-05-01T01:00:00Z", True),
        ("2026-05-01T09:00:00,123Z", "2026-05-01T09:00:00.123Z", True),
        ("2026-05-01T09:00:00.1234567Z", "2026-05-01T09:00:00.1234567Z", True),
        ("2026-05-01T09:00:00.1234567Z", "2026-05-01T09:00:00.123456Z", False),
        ("2026-05-01T09:00:00.1234567Z", "2026-05-01T09:00:00.1234568Z", False),
        ("9999-12-31T23:59:59-05:00", "9999-12-31T23:59:59-05:00", True),
        ("9999-12-31T23:59:59-05:00", "9999-12-31T23:59:59", False),
        ("0001-01-01T00:00:00+08:00", "0001-01-01T00:00:00+08:00", True),
    ],
)
def test_retained_time_keeps_the_supplied_instant_and_precision(provided, retained, accepted):
    assert preserves_time_literals(f"The policy applies from {retained}.", (provided,)) is accepted


@pytest.mark.parametrize("carrier", ["object", "json", "native"])
def test_effective_time_is_not_replaced_by_later_recording_time(carrier):
    content = {
        "statement": "The production port is 6432.",
        "effective_at": "2026-05-01T00:00:00Z",
        "recorded_at": "2026-09-01T09:00:00Z",
    }
    if carrier == "json":
        content = json.dumps(content)
    elif carrier == "native":
        content = {"source_id": "policy", "content": json.dumps(content), "metadata": {}}
    assert source_time_literals(content) == ("2026-05-01T00:00:00Z",)


def test_recording_time_is_used_only_without_an_explicit_fact_time():
    content = {"statement": "The production port is 6432.", "recorded_at": "2026-05-01T09:00:00Z"}
    assert source_time_literals(content) == ("2026-05-01T09:00:00Z",)
    assert source_time_literals({**content, "effective_at": "last spring"}) == ()


@pytest.mark.parametrize(
    "content",
    [
        {"effective_at": "2026-05-01", "facts": ["Port 6432", "Earlier port 5432"]},
        {"statement": "Port 6432", "metadata": {"effective_at": "2026-05-01"}},
        {"statement": "Port 6432", "effective_at": "2026-02-30"},
        {"statement": "Port 6432", "effective_at": "2026-05-01", "other_fact": "Port 5432 in March"},
    ],
)
def test_unassociated_or_invalid_dates_do_not_date_a_fact(content):
    assert source_time_literals(content) == ()


@pytest.mark.parametrize(
    "literal",
    ["2026-05-01T09:00:00.1234567Z", "2026-05-01T09:00:00+0800", "9999-12-31T23:59:59-05:00"],
)
def test_source_and_artifact_time_literals_preserve_the_complete_timestamp(literal):
    assert source_time_literals({"statement": "Port 6432", "effective_at": literal}) == (literal,)
    assert text_time_literals(f"The port changed at {literal}.") == (literal,)


@pytest.mark.parametrize("separator", ["T", " "])
def test_unsupported_time_suffix_does_not_turn_into_a_shorter_literal(separator):
    assert text_time_literals(f"The port changed at 2026-05-01{separator}09:00:00+08:00:30.") == ()


@pytest.mark.parametrize("literal", ["2026-05-01T09:00:00.1234567Z", "2026-05-01T09:00:00+0800"])
def test_reconciliation_rejects_loss_of_selected_artifact_precision_or_timezone(literal):
    ref = ArtifactRef(family="atomic-memory", artifact_id="port-policy", revision=1)
    old = f"The policy uses port 6432 effective {literal}."
    request = AtomicMemoryReconciliationInput(
        proposal=AtomicMemoryComparisonItem(
            item_id="proposal",
            kind="decision",
            text="The policy requires TLS.",
            original_refs=(),
            evidence_ids=("source",),
        ),
        related=(
            AtomicMemoryComparisonItem(
                item_id="old",
                kind="decision",
                text=old,
                original_refs=(AtomicMemoryRead(ref=ref, state=AtomicMemoryStateValue.ACTIVE, state_version=0),),
                evidence_ids=("artifact",),
            ),
        ),
        evidence=(
            AtomicMemoryEvidence(
                evidence_id="source",
                source_ref=SourceRef(source_type="content", source_id="tls"),
                journal_position=2,
                content="The policy requires TLS.",
                source_metadata={},
            ),
            AtomicMemoryArtifactEvidence(
                evidence_id="artifact", artifact_ref=ref, content=AtomicMemoryContent(kind="decision", text=old)
            ),
        ),
    )
    output = AtomicMemoryReconciliationOutput(
        action="revise",
        compared_ids=("old",),
        target_ids=("old",),
        content=AtomicMemoryReconciliationContent(
            kind="decision",
            text="The policy uses port 6432 effective 2026-05-01T09:00:00 and requires TLS.",
            description="Port policy",
        ),
        evidence_ids=("source", "artifact"),
        reason="Keep the port policy and the new TLS requirement.",
    )

    with pytest.raises(InvalidInferenceOutputError, match="retain explicit time qualifiers"):
        validate_reconciliation_decision(request, output)
