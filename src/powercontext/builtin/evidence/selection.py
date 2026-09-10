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

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.evidence.models import EvidenceManifest, EvidenceResolutionError, unique_references
from powercontext.builtin.evidence.resolver import evidence_id
from powercontext.sources import SourceRef


class SelectedEvidence(BaseModel):
    sources: tuple[SourceRef, ...]
    artifacts: tuple[ArtifactRef, ...]
    memory_citations: tuple[MemoryCitation, ...]


def select_evidence(
    manifest: EvidenceManifest,
    used: tuple[str, ...],
    *,
    skill: bool,
    target: ArtifactRef | None,
) -> SelectedEvidence:
    nodes = {node.evidence_id: node for node in manifest.nodes}
    for node_id in used:
        node = nodes.get(node_id)
        if node is None or node.role in {"unresolved", "lineage_only"} or (skill and node.kind == "memory"):
            raise EvidenceResolutionError("invalid_generation_output")
    chosen = set(used)
    origins = (*manifest.artifacts, *manifest.memory_citations)
    # A model that cites a root must retain the exact selected entry/Artifact path.
    for origin in origins:
        origin_id = evidence_id(origin)
        if _reachable(origin_id, manifest) & chosen:
            chosen.add(origin_id)
    dependencies = set().union(*(_reachable(node_id, manifest) for node_id in chosen))
    sources = tuple(
        node.source
        for key, node in nodes.items()
        if key in dependencies and node.role == "root" and node.source is not None
    )
    artifacts = tuple(
        node.artifact
        for key, node in nodes.items()
        if key in chosen and node.kind == "experience" and node.artifact is not None
    )
    citations = (
        ()
        if skill
        else tuple(citation for key, node in nodes.items() if key in chosen for citation in node.memory_citations)
    )
    if target is not None:
        artifacts = unique_references((*artifacts, target))
    memory_derived = any(nodes[key].kind == "memory" for key in dependencies)
    if memory_derived and not sources:
        raise EvidenceResolutionError("needs_evidence")
    if skill and not artifacts:
        raise EvidenceResolutionError("invalid_generation_output")
    if len(sources) + len(artifacts) + len(citations) > 32:
        raise EvidenceResolutionError("evidence_limit_exceeded")
    return SelectedEvidence(sources=sources, artifacts=artifacts, memory_citations=citations)


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
