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

"""Content-free proposal identity and immutable reviewer provenance."""

import json
from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import BaseModel

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.dream.bindings import DREAM_OPERATIONS
from powercontext.builtin.dream.models import DREAM_PROMPT_VERSION, DreamRecord
from powercontext.builtin.evidence.models import (
    EvidenceManifest,
    EvidenceNode,
    QualifiedOrigin,
    ResolvedEvidence,
    content_digest,
)
from powercontext.builtin.persistence.candidates import proposal_digest
from powercontext.builtin.review.models import CandidateAudit
from powercontext.sources import SourceRef


def validation_policy_digest(record: DreamRecord) -> str:
    spec = next(spec for spec in DREAM_OPERATIONS if spec.operation == record.run.operation)
    return _digest({
        "operation": spec.operation,
        "spec_version": spec.spec_version,
        "prompt_version": DREAM_PROMPT_VERSION,
        "profile_policy": None if record.profile_policy is None else record.profile_policy.model_dump(mode="json"),
    })


def proposal_fingerprint(record: DreamRecord, evidence: ResolvedEvidence) -> str:
    nodes = {node.evidence_id: node for node in evidence.manifest.nodes}
    roots = sorted((node.evidence_id, node.digest) for node in nodes.values() if node.role == "root")
    # Keep root-backed derived artifacts deduplicated by their root Source, but
    # retain every independent node that cannot be traced to one. This matters
    # when rooted and rootless evidence are supplied together: adding the latter
    # must invalidate reuse of a proposal built from the former alone.
    independent = sorted(
        (node.evidence_id, node.digest)
        for node in nodes.values()
        if node.role != "target" and node.role != "root" and not _reaches_root(node.evidence_id, nodes, evidence)
    )
    roots = sorted({*roots, *independent})
    request = record.request
    if request is None:
        raise ValueError("historical Dream runs cannot propose candidates")  # noqa: TRY003
    return _digest({
        "operation": request.operation,
        "target": None if request.target is None else request.target.model_dump(mode="json"),
        "tag_target": None if request.tag_target is None else request.tag_target.model_dump(mode="json"),
        "roots": roots,
        "policy": validation_policy_digest(record),
    })


def candidate_audit(
    record: DreamRecord,
    proposal: BaseModel,
    *,
    adopted_origins: tuple[QualifiedOrigin, ...] = (),
) -> CandidateAudit:
    """Server-owned provenance for one proposal, including a shared run's adopted support.

    ``adopted_origins`` is only supplied for a run bound to a share snapshot; it is the
    exact support the proposal stands on, each with its owning Scope, never every pinned
    input. An ordinary proposal keeps the unchanged audit shape.
    """

    spec = next(spec for spec in DREAM_OPERATIONS if spec.operation == record.run.operation)
    return CandidateAudit(
        operation=record.run.operation,
        dream_run_id=record.run.run_id,
        spec_version=spec.spec_version,
        proposal_digest=proposal_digest(proposal),
        evidence_manifest_ref=f"dream:{record.run.run_id}",
        validation_policy_digest=validation_policy_digest(record),
        proposal_fingerprint=record.proposal_fingerprint,
        share_snapshot_id=record.share_snapshot_id,
        adopted_origins=adopted_origins,
    )


@dataclass(frozen=True, slots=True)
class SharedAdoption:
    """The one server-owned adoption binding for a pinned shared proposal.

    Built inside the existing Dream transaction from the durable record, the persisted input
    manifest and the evidence the model actually cited. Nothing here is supplied by an
    outside caller, so a Candidate cannot assert support the run did not select, and the
    binding reproduces exact qualified identity rather than bare references.
    """

    run_id: str
    snapshot_id: str
    operation: str
    direct: tuple[QualifiedOrigin, ...]
    reachable: tuple[QualifiedOrigin, ...]
    local_sources: tuple[SourceRef, ...]
    local_artifacts: tuple[ArtifactRef, ...]
    target: ArtifactRef | None
    validation_policy_digest: str

    @property
    def adopted(self) -> tuple[QualifiedOrigin, ...]:
        """Everything the revision stands on: direct inputs plus reachable support."""

        return unique_origins((*self.direct, *self.reachable))


def unique_origins(values: tuple[QualifiedOrigin, ...], /) -> tuple[QualifiedOrigin, ...]:
    """Deduplicate by exact qualified identity, keeping first-seen order."""

    seen: set[tuple[str, str]] = set()
    result: list[QualifiedOrigin] = []
    for origin in values:
        key = (origin.scope_id, origin.reference.model_dump_json())
        if key in seen:
            continue
        seen.add(key)
        result.append(origin)
    return tuple(result)


def bind_shared_adoption(
    record: DreamRecord,
    *,
    cited: tuple[str, ...],
    manifest: EvidenceManifest,
) -> SharedAdoption:
    """Derive the adoption binding from durable run state and the cited evidence.

    ``cited`` are the evidence ids the model actually selected. Everything else — direct
    inputs, the reachable roots and Artifact paths, the local bare slots and the policy
    digest — is reproduced from the persisted manifest and the record itself.
    """

    if record.share_snapshot_id is None:
        raise ValueError("an adoption binding requires a pinned run")  # noqa: TRY003
    if record.request is None:
        raise ValueError("an adoption binding requires an executable request")  # noqa: TRY003
    from powercontext.builtin.evidence.selection import qualified_direct_origins, select_evidence

    scope_id = record.run.scope_id
    selected = select_evidence(
        manifest,
        cited,
        skill=record.run.operation == "derive_skill",
        target=record.request.target,
        scope_id=scope_id,
    )
    direct = qualified_direct_origins(manifest, cited, scope_id=scope_id)
    direct_keys = {(origin.scope_id, origin.reference.model_dump_json()) for origin in direct}
    reachable = tuple(
        origin
        for origin in selected.adopted
        if (origin.scope_id, origin.reference.model_dump_json()) not in direct_keys
    )
    return SharedAdoption(
        run_id=record.run.run_id,
        snapshot_id=record.share_snapshot_id,
        operation=record.run.operation,
        direct=direct,
        reachable=reachable,
        local_sources=selected.sources,
        local_artifacts=selected.artifacts,
        target=record.request.target,
        validation_policy_digest=validation_policy_digest(record),
    )


def shared_candidate_audit(
    adoption: SharedAdoption,
    proposal: BaseModel,
    /,
) -> CandidateAudit:
    """Build the audit for one pinned shared proposal from its adoption binding.

    No digest or policy is supplied by the caller: identity comes from the binding, which was
    itself derived from durable run state inside the admitting transaction.
    """

    spec = next(spec for spec in DREAM_OPERATIONS if spec.operation == adoption.operation)
    return CandidateAudit(
        operation=adoption.operation,
        dream_run_id=adoption.run_id,
        spec_version=spec.spec_version,
        proposal_digest=proposal_digest(proposal),
        evidence_manifest_ref=f"dream:{adoption.run_id}",
        validation_policy_digest=adoption.validation_policy_digest,
        proposal_fingerprint=None,
        share_snapshot_id=adoption.snapshot_id,
        adopted_origins=adoption.adopted,
    )


def _digest(value: object) -> str:
    return content_digest(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())


def _reaches_root(node_id: str, nodes: Mapping[str, EvidenceNode], evidence: ResolvedEvidence) -> bool:
    """Return whether a manifest node has a root Source in its lineage."""

    upstream: dict[str, list[str]] = {}
    for edge in evidence.manifest.edges:
        upstream.setdefault(edge.derived_id, []).append(edge.upstream_id)
    pending = [node_id]
    visited: set[str] = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        node = nodes.get(current)
        if node is None:
            continue
        if node.role == "root":
            return True
        pending.extend(upstream.get(current, ()))
    return False
