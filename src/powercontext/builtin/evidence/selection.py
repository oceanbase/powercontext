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

"""Map model evidence IDs to exact, reviewable provenance."""

from __future__ import annotations

from pydantic import BaseModel

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.evidence.models import (
    EvidenceManifest,
    EvidenceNode,
    EvidenceResolutionError,
    QualifiedOrigin,
    reference_key,
    unique_references,
)
from powercontext.sources import SourceRef

# Node kinds whose Artifact revision is real support, not a comparison view.
SUPPORTING_ARTIFACT_KINDS = frozenset({
    "experience",
    "memory",
    "skill",
    "profile",
    "topic_memory",
    "handoff",
})


class SelectedEvidence(BaseModel):
    sources: tuple[SourceRef, ...]
    artifacts: tuple[ArtifactRef, ...]
    # What the review must bind: exact origins for the cited support, including the
    # reachable root dependencies and adopted Artifact paths. Never all admitted input.
    adopted: tuple[QualifiedOrigin, ...] = ()


def select_evidence(
    manifest: EvidenceManifest,
    used: tuple[str, ...],
    *,
    skill: bool,
    target: ArtifactRef | None,
    catalog: bool = False,
) -> SelectedEvidence:
    nodes = {node.evidence_id: node for node in manifest.nodes}
    for node_id in used:
        node = nodes.get(node_id)
        if node is None or node.role in {"unresolved", "lineage_only"} or (skill and node.kind == "memory"):
            raise EvidenceResolutionError("invalid_generation_output")
    # Targets may be named as comparison context, but cannot support their own revision.
    chosen = {node_id for node_id in used if nodes[node_id].role != "target"}
    if not chosen:
        raise EvidenceResolutionError("needs_evidence")
    # A model that cites a root must retain the exact selected entry/Artifact path. The
    # manifest already names each admitted input's node id, so no identity is recomputed
    # from a bare reference here.
    for node_id in admitted_artifact_ids(manifest):
        if nodes[node_id].role != "target" and _reachable(node_id, manifest) & chosen:
            chosen.add(node_id)
    dependencies = set().union(*(_reachable(node_id, manifest) for node_id in chosen))
    sources = tuple(
        node.source
        for key, node in nodes.items()
        if key in dependencies and node.role == "root" and node.source is not None
    )
    artifact_kinds = SUPPORTING_ARTIFACT_KINDS if catalog else frozenset({"experience", "memory"})
    artifacts = tuple(
        node.artifact
        for key, node in nodes.items()
        if key in chosen and node.kind in artifact_kinds and node.artifact is not None
    )
    if isinstance(target, ArtifactRef):
        artifacts = unique_references((*artifacts, target))
    memory_derived = any(nodes[key].kind == "memory" for key in dependencies)
    if memory_derived and not sources:
        raise EvidenceResolutionError("needs_evidence")
    if skill and not artifacts:
        raise EvidenceResolutionError("invalid_generation_output")
    if len(sources) + len(artifacts) > 32:
        raise EvidenceResolutionError("evidence_limit_exceeded")
    adopted = tuple(
        QualifiedOrigin(
            scope_id=nodes[key].origin_scope_id or "",
            source=nodes[key].source,
            artifact=nodes[key].artifact,
            digest=nodes[key].digest,
        )
        for key in sorted(chosen | dependencies)
        if _adoptable(nodes[key])
    )
    return SelectedEvidence(sources=sources, artifacts=artifacts, adopted=adopted)


def _adoptable(node: EvidenceNode) -> bool:
    """Whether this node is support the revision stands on, rather than a view of it."""

    if node.role in {"unresolved", "lineage_only", "target"}:
        return False
    return node.source is not None or (node.artifact is not None and node.kind in SUPPORTING_ARTIFACT_KINDS)


def admitted_artifact_ids(manifest: EvidenceManifest) -> tuple[str, ...]:
    """Node ids of the Artifact revisions this manifest admitted as direct input.

    Only the manifest's declared ``artifacts`` count, exactly as before: a node merely
    reachable through lineage is not a selected input. The id is taken from the node, so
    the owning Scope is never re-derived from a bare reference.
    """

    admitted = {reference_key(ref) for ref in manifest.artifacts}
    return tuple(
        node.evidence_id
        for node in manifest.nodes
        if node.artifact is not None and reference_key(node.artifact) in admitted
    )


def _reachable(start: str, manifest: EvidenceManifest) -> set[str]:
    visited: set[str] = set()
    pending = [start]
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        pending.extend(edge.upstream_id for edge in manifest.edges if edge.derived_id == current)
    return visited
