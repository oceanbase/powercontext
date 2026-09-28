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
from powercontext.builtin.artifacts.profile.models import normalize_profile_markdown
from powercontext.builtin.artifacts.profile.service import ProfileGenerationInput, ProfileGenerationOutput
from powercontext.builtin.artifacts.topic_memory.generation import (
    TopicMemoryEvolveInput,
    TopicMemoryEvolveOutput,
    TopicMemoryGlobalInput,
    TopicMemoryGlobalOutput,
    TopicMemoryPlannerInput,
    TopicMemoryPlannerOutput,
    TopicMemoryProbeInput,
    TopicMemoryProbeOutput,
    TopicMemoryProposal,
    TopicMemoryReconcileInput,
    TopicMemoryReconcileOutput,
    TopicMemoryReductionInput,
    TopicMemoryReductionOutput,
    TopicMemoryTemporaryInput,
    TopicMemoryTemporaryOutput,
)
from powercontext.sources import SourceRef


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError("demonstration violates the operation's reference or output contract")  # noqa: TRY003


def _identities(values: Iterable[str]) -> set[str]:
    items = tuple(values)
    _require(all(item.strip() and item == item.strip() for item in items) and len(set(items)) == len(items))
    return set(items)


def validate_demonstration(value: BaseModel, output: BaseModel) -> None:  # noqa: C901 - one branch per operation family
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
    elif isinstance(value, ProfileGenerationInput) and isinstance(output, ProfileGenerationOutput):
        if output.content is not None:
            normalize_profile_markdown(output.content)
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
    elif isinstance(value, TopicMemoryProbeInput) and isinstance(output, TopicMemoryProbeOutput):
        _topic_memory_probe(value, output)
    elif isinstance(value, TopicMemoryGlobalInput) and isinstance(output, TopicMemoryGlobalOutput):
        _topic_memory_global(value, output)
    elif isinstance(value, TopicMemoryPlannerInput) and isinstance(output, TopicMemoryPlannerOutput):
        _topic_memory_planner(value, output)
    elif isinstance(value, TopicMemoryEvolveInput) and isinstance(output, TopicMemoryEvolveOutput):
        _topic_memory_evolve(value, output)
    elif isinstance(value, TopicMemoryTemporaryInput) and isinstance(output, TopicMemoryTemporaryOutput):
        _topic_memory_temporary(value, output)
    elif isinstance(value, TopicMemoryReductionInput) and isinstance(output, TopicMemoryReductionOutput):
        _topic_memory_reduce(value, output)
    elif isinstance(value, TopicMemoryReconcileInput) and isinstance(output, TopicMemoryReconcileOutput):
        _topic_memory_reconcile(value, output)


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


def _topic_memory_probe(value: TopicMemoryProbeInput, output: TopicMemoryProbeOutput) -> None:
    evidence = _identities(item.evidence_id for item in value.evidence)
    for probe in output.probes:
        _require(bool(probe.evidence_ids) and set(probe.evidence_ids) <= evidence)


def _topic_memory_global(value: TopicMemoryGlobalInput, output: TopicMemoryGlobalOutput) -> None:
    evidence = _identities(item.evidence_id for item in value.evidence)
    candidates = {slot.candidate_id for slot in value.historical} | {
        candidate_id for probe in value.probes for candidate_id in probe.candidate_ids
    }
    targets: set[str] = set()
    proposal_ids: set[str] = set()
    for proposal in output.proposals:
        _require(bool(proposal.evidence_ids) and set(proposal.evidence_ids) <= evidence)
        if proposal.candidate_id is not None:
            _require(proposal.candidate_id in candidates and proposal.candidate_id not in targets)
            targets.add(proposal.candidate_id)
        if proposal.proposal_id is not None:
            _require(proposal.proposal_id not in proposal_ids)
            proposal_ids.add(proposal.proposal_id)


def _topic_memory_planner(value: TopicMemoryPlannerInput, output: TopicMemoryPlannerOutput) -> None:
    probes = {probe.probe_id: probe for probe in value.probes}
    _identities(probes)
    candidates = {preview.candidate_id for preview in value.historical} | {
        candidate_id for probe in value.probes for candidate_id in probe.candidate_ids
    }
    assigned: set[str] = set()
    item_by_probe: dict[str, int] = {}
    for index, item in enumerate(output.items):
        for probe_id in item.probe_ids:
            _require(probe_id in probes and probe_id not in assigned)
            assigned.add(probe_id)
            item_by_probe[probe_id] = index
    _require(assigned == set(probes))
    selected: set[str] = set()
    for item in output.items:
        if item.candidate_id is None:
            continue
        _require(item.candidate_id in candidates and item.candidate_id not in selected)
        selected.add(item.candidate_id)
        for probe_id in item.probe_ids:
            _require(item.candidate_id in probes[probe_id].candidate_ids)
    for probe in value.probes:
        # The runtime rejects plans that split probes sharing a candidate across
        # items, whether or not any item selects that candidate as its target.
        for candidate_id in probe.candidate_ids:
            items = {item_by_probe[other.probe_id] for other in value.probes if candidate_id in other.candidate_ids}
            _require(len(items) == 1)


def _topic_memory_evolve(value: TopicMemoryEvolveInput, output: TopicMemoryEvolveOutput) -> None:
    evidence = {item.evidence_id for item in value.evidence} | {
        evidence_id for temporary in value.temporary for evidence_id in temporary.evidence_ids
    }
    proposal = output.proposal
    if proposal is not None:
        _require(bool(proposal.evidence_ids) and set(proposal.evidence_ids) <= evidence)
        _require(
            proposal.candidate_id is None
            if value.historical is None
            else proposal.candidate_id == value.historical.candidate_id
        )


def _topic_memory_temporary(value: TopicMemoryTemporaryInput, output: TopicMemoryTemporaryOutput) -> None:
    evidence = _identities(item.evidence_id for item in value.evidence)
    proposal_ids: set[str] = set()
    for proposal in output.proposals:
        _require(proposal.candidate_id is None)
        _require(bool(proposal.evidence_ids) and set(proposal.evidence_ids) <= evidence)
        if proposal.proposal_id is not None:
            _require(proposal.proposal_id not in proposal_ids)
            proposal_ids.add(proposal.proposal_id)


def _topic_memory_reduce(value: TopicMemoryReductionInput, output: TopicMemoryReductionOutput) -> None:
    temporary = bool(value.temporary)
    items = value.temporary if temporary else value.probes
    result = output.temporary if temporary else output.probe
    other = output.probe if temporary else output.temporary
    if result is None or other is not None:
        raise ValueError("demonstration violates the operation's reference or output contract")  # noqa: TRY003
    _require(sorted(output.covered_indices) == list(range(len(items))))
    _require(set(result.evidence_ids) == {evidence_id for item in items for evidence_id in item.evidence_ids})
    if isinstance(result, TopicMemoryProposal):
        _require(result.candidate_id is None and result.proposal_id is None)


def _topic_memory_reconcile(value: TopicMemoryReconcileInput, output: TopicMemoryReconcileOutput) -> None:
    inputs = value.proposals
    outputs = output.proposals
    input_ids = {item.proposal_id for item in inputs}
    input_targets = {item.candidate_id for item in inputs if item.candidate_id is not None}
    # The runtime allows an unbound proposal to gain a target from the supplied
    # historical slots; existing assignments must stay fixed.
    allowed_targets = input_targets | {slot.candidate_id for slot in value.historical}
    input_evidence = {evidence_id for item in inputs for evidence_id in item.evidence_ids}
    output_ids = [item.proposal_id for item in outputs]
    _require(None not in output_ids and set(output_ids) <= input_ids and len(output_ids) == len(set(output_ids)))
    output_targets = [item.candidate_id for item in outputs if item.candidate_id is not None]
    _require(
        input_targets <= set(output_targets)
        and set(output_targets) <= allowed_targets
        and len(output_targets) == len(set(output_targets))
    )
    output_by_id = {item.proposal_id: item for item in outputs if item.proposal_id is not None}
    for item in inputs:
        if item.candidate_id is not None:
            _require(
                item.proposal_id in output_by_id and output_by_id[item.proposal_id].candidate_id == item.candidate_id
            )
    for item in outputs:
        _require(set(item.evidence_ids) <= input_evidence)
