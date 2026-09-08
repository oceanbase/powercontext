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


"""Operation-local semantic checks for desired demonstration outputs."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel

from powercontext.builtin.artifacts.experience import ExperienceIncubationInput, ExperienceIncubationOutput
from powercontext.builtin.artifacts.generation import ArtifactGenerationInput, GenerationEvidenceKind
from powercontext.builtin.artifacts.handoff import (
    HandoffDraft,
    HandoffGenerationInput,
    HandoffGenerationOutput,
    HandoffOmission,
    HandoffSourceCitation,
    HandoffStatement,
    PrepareHandoff,
)
from powercontext.builtin.artifacts.handoff.generation import HandoffGenerationStatement
from powercontext.builtin.artifacts.memory import (
    MemoryExtractionInput,
    MemoryExtractionOutput,
    MemoryRerankInput,
    MemoryRerankOutput,
)
from powercontext.sources import SourceRef


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError("demonstration violates the operation's reference or output contract")  # noqa: TRY003


def _identities(values: Iterable[str]) -> set[str]:
    items = tuple(values)
    _require(all(item.strip() and item == item.strip() for item in items) and len(set(items)) == len(items))
    return set(items)


def validate_demonstration(value: BaseModel, output: BaseModel) -> None:
    """Enforce relationships that independent input/output JSON schemas cannot express."""
    if isinstance(value, MemoryExtractionInput) and isinstance(output, MemoryExtractionOutput):
        _memory_extraction(value, output)
    elif isinstance(value, MemoryRerankInput) and isinstance(output, MemoryRerankOutput):
        ranks = tuple(candidate.rank for candidate in value.candidates)
        _require(bool(ranks) and ranks == tuple(range(1, len(ranks) + 1)))
        selected = output.selected_ranks
        _require(0 < len(selected) <= value.max_results and len(set(selected)) == len(selected))
        _require(set(selected) <= set(ranks))
    elif isinstance(value, ExperienceIncubationInput) and isinstance(output, ExperienceIncubationOutput):
        evidence = _identities(item.evidence_id for item in value.evidence)
        for candidate in output.candidates:
            _require(bool(candidate.evidence_ids) and set(candidate.evidence_ids) <= evidence)
    elif isinstance(value, ArtifactGenerationInput):
        _identities(item.evidence_id for item in value.evidence)
        if value.target_evidence_id is not None:
            _require(
                any(
                    item.evidence_id == value.target_evidence_id and item.kind == GenerationEvidenceKind.ARTIFACT
                    for item in value.evidence
                )
            )
    elif isinstance(value, HandoffGenerationInput) and isinstance(output, HandoffGenerationOutput):
        _handoff(value, output)


def _memory_extraction(value: MemoryExtractionInput, output: MemoryExtractionOutput) -> None:
    evidence = _identities(item.evidence_id for item in value.evidence)
    entries = _identities(item.entry_id for item in value.current_entries)
    revised: set[str] = set()
    for candidate in output.candidates:
        _require(bool(candidate.text.strip()))
        _require(bool(candidate.evidence_ids) and set(candidate.evidence_ids) <= evidence)
        if candidate.intent == "add":
            _require(candidate.entry_id is None)
        else:
            _require(candidate.entry_id in entries and candidate.entry_id not in revised)
            if candidate.entry_id is not None:
                revised.add(candidate.entry_id)


def _handoff(value: HandoffGenerationInput, output: HandoffGenerationOutput) -> None:
    evidence = _identities(item.evidence_id for item in value.evidence)
    # Demonstrations have operation-local IDs, not persisted references. Synthetic citations
    # validate Draft structure without pretending the examples belong to a real Scope.
    citations = {
        evidence_id: HandoffSourceCitation(source_ref=SourceRef(source_type="content", source_id=f"demo-{index}"))
        for index, evidence_id in enumerate(sorted(evidence))
    }
    PrepareHandoff(objective=value.objective, evidence=tuple(citations.values()), max_bytes=value.max_bytes)

    def statement(item: HandoffGenerationStatement) -> HandoffStatement:
        _require(bool(item.evidence_ids) and set(item.evidence_ids) <= evidence)
        return HandoffStatement(
            text=item.text, citations=tuple(citations[identifier] for identifier in dict.fromkeys(item.evidence_ids))
        )

    omissions = []
    for item in output.omissions:
        _require(item.evidence_id is None or item.evidence_id in evidence)
        omissions.append(
            HandoffOmission(text=item.text, citation=None if item.evidence_id is None else citations[item.evidence_id])
        )
    HandoffDraft(
        objective=value.objective,
        state=tuple(statement(item) for item in output.state),
        disposition=output.disposition,
        next_action=None if output.next_action is None else statement(output.next_action),
        omissions=tuple(omissions),
    )
