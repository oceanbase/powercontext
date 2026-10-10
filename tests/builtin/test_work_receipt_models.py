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

"""Unavailable receipt addresses remain historical descriptions, never new evidence."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.handoff import HandoffArtifactCitation, HandoffSourceCitation, PrepareHandoff
from powercontext.builtin.work import HandoffReceipt, WorkClaim
from powercontext.sources import SourceRef


def _receipt(reference: dict[str, Any]) -> dict[str, Any]:
    return {
        "receiver": "next-agent",
        "status": "needs_clarification",
        "selection": "exact",
        "selected_revision": {"family": "handoff", "artifact_id": "handoff-1", "revision": 1},
        "evidence_status": "unavailable",
        "unavailable_evidence": [{"kind": "artifact", "artifact_ref": reference}],
        "message": "Evidence was unavailable.",
    }


def test_receipt_preserves_unavailable_collection_address_without_reenabling_evidence() -> None:
    reference = {"family": "memory", "artifact_id": "old-collection", "revision": 2}
    receipt = HandoffReceipt.model_validate_json(json.dumps(_receipt(reference)))
    assert receipt.model_dump(mode="json")["unavailable_evidence"] == _receipt(reference)["unavailable_evidence"]
    assert "historical_data" not in receipt.model_dump(mode="json")
    assert HandoffReceipt.model_validate_json(receipt.model_dump_json()) == receipt

    for citation in (receipt.unavailable_evidence[0], receipt.unavailable_evidence[0].model_dump()):
        with pytest.raises(ValidationError):
            PrepareHandoff.model_validate({"objective": "Continue work.", "evidence": (citation,)})
        with pytest.raises(ValidationError):
            WorkClaim.model_validate({"text": "The work succeeded.", "basis": "verified", "evidence": (citation,)})


def test_new_receipt_accepts_resolved_citation_values_and_preserves_sources() -> None:
    artifact = HandoffArtifactCitation(
        artifact_ref=ArtifactRef(family="atomic-memory", artifact_id="entry-1", revision=2)
    )
    source = HandoffSourceCitation(source_ref=SourceRef(source_type="content", source_id="source-1"))
    payload = _receipt(artifact.artifact_ref.model_dump())
    payload["unavailable_evidence"] = (artifact, source)
    receipt = HandoffReceipt.model_validate(payload)
    assert receipt.unavailable_evidence[1] == source
    assert receipt.model_dump(mode="json")["unavailable_evidence"] == [
        artifact.model_dump(mode="json"),
        source.model_dump(mode="json"),
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("family", ""),
        ("family", " memory"),
        ("family", "x" * 129),
        ("artifact_id", ""),
        ("artifact_id", "old-collection "),
        ("artifact_id", "x" * 129),
        ("revision", 0),
        ("revision", True),
        ("revision", "2"),
    ],
)
def test_receipt_unavailable_addresses_keep_identity_constraints(field: str, value: Any) -> None:
    reference = {"family": "memory", "artifact_id": "old-collection", "revision": 2, field: value}
    with pytest.raises(ValidationError, match=field):
        HandoffReceipt.model_validate_json(json.dumps(_receipt(reference)))
