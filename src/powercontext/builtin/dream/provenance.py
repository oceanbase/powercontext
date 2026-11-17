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

from pydantic import BaseModel

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.dream.bindings import DREAM_OPERATIONS
from powercontext.builtin.dream.models import DREAM_PROMPT_VERSION, DreamRecord
from powercontext.builtin.evidence.models import EvidenceNode, QualifiedOrigin, ResolvedEvidence, content_digest
from powercontext.builtin.persistence.candidates import proposal_digest
from powercontext.builtin.review.models import CandidateAudit


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


def shared_candidate_audit(
    *,
    run_id: str,
    snapshot_id: str,
    operation: str,
    proposal: BaseModel,
    adopted_origins: tuple[QualifiedOrigin, ...],
    target: ArtifactRef | None,
    validation_policy_digest_value: str,
) -> CandidateAudit:
    """Build the audit for one pinned shared proposal from durable identity only.

    The caller supplies no digest and no policy: the run id and pin are the identity, the
    proposal digest is computed here, and the adopted set is the verified support. An
    ordinary audit keeps its own construction path, so this never changes existing records.
    """

    spec = next(spec for spec in DREAM_OPERATIONS if spec.operation == operation)
    return CandidateAudit(
        operation=operation,
        dream_run_id=run_id,
        spec_version=spec.spec_version,
        proposal_digest=proposal_digest(proposal),
        evidence_manifest_ref=f"dream:{run_id}",
        # The run's own policy digest, so approval rechecks against the same policy the
        # generation was admitted under rather than an invented one.
        validation_policy_digest=validation_policy_digest_value,
        proposal_fingerprint=None,
        share_snapshot_id=snapshot_id,
        adopted_origins=adopted_origins,
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
