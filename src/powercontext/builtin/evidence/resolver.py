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

"""Resolve exact entry and Artifact lineage without broadening input authority."""

from __future__ import annotations

from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactLineage, ArtifactRef, MemoryCitation
from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemory, AtomicMemoryStateValue
from powercontext.builtin.artifacts.experience import Experience
from powercontext.builtin.artifacts.memory import Memory, MemoryEntryVersion
from powercontext.builtin.artifacts.memory.errors import InvalidMemoryCitationError, MemoryEntryNotFoundError
from powercontext.builtin.evidence.models import (
    EVIDENCE_TRANSFORM_VERSION,
    EvidenceEdge,
    EvidenceLimits,
    EvidenceManifest,
    EvidenceNode,
    EvidenceProjection,
    EvidenceResolutionError,
    ProjectedEvidence,
    ResolvedEvidence,
    RootEvidenceGroup,
    content_digest,
    reference_key,
    unique_references,
)
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.atomic_memory import AtomicMemoryStateRepository
from powercontext.builtin.persistence.atomic_memory_identity import legacy_entry_artifact_id
from powercontext.builtin.persistence.atomic_memory_legacy_evidence import read_imported_memory_evidence
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.generation_sources import GenerationSourceAccess
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.source_eligibility import SourceNotEligibleError
from powercontext.errors import InvalidSourceProjectionError, SourceProjectionNotFoundError
from powercontext.sources import Source, SourceRef

EvidenceReference = ArtifactRef | MemoryCitation | SourceRef
EvidenceChild = tuple[EvidenceReference, str | None]
EvidenceAuthorizer = Callable[[EvidenceReference], Awaitable[None]]
AuthorizationContext = Callable[[], AbstractAsyncContextManager[None]]
ScopedEvidenceAuthorizer = Callable[[str, EvidenceReference], Awaitable[None]]
MemoryReader = Callable[[AsyncConnection, MemoryCitation], Awaitable[MemoryEntryVersion]]
RootIdentityResolver = Callable[[SourceRef, Source], str | None]


@dataclass
class _Traversal:
    project: bool = True
    nodes: dict[str, EvidenceNode] = field(default_factory=dict)
    bodies: dict[str, str] = field(default_factory=dict)
    edges: set[tuple[str, str]] = field(default_factory=set)
    groups: dict[str, tuple[str, bool]] = field(default_factory=dict)


class EvidenceResolver:
    def __init__(
        self,
        *,
        scope_id: str,
        sources: SourceRepository,
        artifacts: ArtifactRepository,
        memory_reader: MemoryReader,
        authorize: EvidenceAuthorizer | None = None,
        root_identity: RootIdentityResolver | None = None,
        source_projector: Callable[[Source], str] | None = None,
        limits: EvidenceLimits | None = None,
    ) -> None:
        self.scope_id = scope_id
        self.sources = sources
        self.generation_sources = GenerationSourceAccess(sources)
        self.artifacts = artifacts
        self.memory_reader = memory_reader
        self.authorize = authorize
        self.root_identity = root_identity
        self.source_projector = source_projector
        self.limits = EvidenceLimits() if limits is None else limits

    async def validate(
        self,
        connection: AsyncConnection,
        *,
        sources: tuple[SourceRef, ...] = (),
        artifacts: tuple[ArtifactRef, ...] = (),
        memory_citations: tuple[MemoryCitation, ...] = (),
    ) -> tuple[SourceRef, ...]:
        """Validate lineage and return eligible Source roots of directly selected Memory evidence."""

        refs: tuple[EvidenceReference, ...] = (*sources, *artifacts, *memory_citations)
        state = await self._validate_lineage(connection, refs)
        owners = tuple(citation for node in state.nodes.values() for citation in node.memory_citations)
        atomic_refs = tuple(
            node.artifact
            for node in state.nodes.values()
            if node.artifact is not None and node.artifact.family == "atomic-memory"
        )
        if owners or atomic_refs:
            # Immutable lineage discovers the owners; current reads under ordered
            # head locks serialize approval against entry deactivation.
            await self._lock_memories(connection, owners, atomic_refs)
            state = await self._validate_lineage(connection, refs, locked=True)
        roots: set[str] = set()
        memory_origins = (*memory_citations, *(ref for ref in artifacts if ref.family == "atomic-memory"))
        for origin in memory_origins:
            roots.update(root_ids(evidence_id(origin), state.nodes, state.edges))
        return tuple(source for key in sorted(roots) if (source := state.nodes[key].source) is not None)

    async def _validate_lineage(
        self,
        connection: AsyncConnection,
        refs: tuple[EvidenceReference, ...],
        *,
        locked: bool = False,
    ) -> _Traversal:
        state = _Traversal(project=False)
        pending: deque[tuple[EvidenceReference, bool, str | None]] = deque((ref, True, None) for ref in refs)
        visited: set[tuple[str, str | None]] = set()
        while pending:
            ref, direct, history_owner = pending.popleft()
            key = (reference_key(ref), history_owner)
            if key in visited:
                continue
            visited.add(key)
            await self._authorize_reference(connection, ref)
            try:
                if isinstance(ref, ArtifactRef):
                    node, children = await self._read_review_artifact(
                        connection, ref, direct=direct, locked=locked, history_owner=history_owner
                    )
                else:
                    node, _, children = await self._read(
                        connection, ref, state, direct=direct, locked=locked, history_owner=history_owner
                    )
            except RepositoryNotFoundError as error:
                raise EvidenceResolutionError("reference_not_found" if direct else "evidence_unavailable") from error
            except (InvalidMemoryCitationError, MemoryEntryNotFoundError) as error:
                raise EvidenceResolutionError("invalid_memory_citation") from error
            previous = state.nodes.get(node.evidence_id)
            if previous is not None:
                node = previous.model_copy(
                    update={
                        "memory_citations": unique_references((*previous.memory_citations, *node.memory_citations)),
                    }
                )
            state.nodes[node.evidence_id] = node
            state.edges.update((node.evidence_id, evidence_id(child)) for child, _ in children)
            pending.extend((child, False, owner) for child, owner in children)
        return state

    async def _read_review_artifact(
        self,
        connection: AsyncConnection,
        ref: ArtifactRef,
        *,
        direct: bool = True,
        locked: bool = False,
        history_owner: str | None = None,
    ) -> tuple[EvidenceNode, tuple[EvidenceChild, ...]]:
        """Follow local Review lineage independently of Dream's supported input Families."""

        artifact = await self.artifacts.get(connection, self.scope_id, ref)
        if isinstance(artifact, AtomicMemory):
            node, _, children = await self._read_atomic(
                connection, ref, artifact, locked=locked, history_owner=history_owner
            )
            return node, children
        children: tuple[EvidenceChild, ...] = ()
        if ref.family != "prompt" and artifact.lineage.publication_source is None:
            children = _lineage_children(artifact)
        return (
            EvidenceNode(
                evidence_id=evidence_id(ref),
                kind="unresolved",
                artifact=ref,
                digest=content_digest(artifact.model_dump_json().encode()),
                role="lineage_only",
            ),
            children,
        )

    async def resolve(
        self,
        connection: AsyncConnection,
        *,
        sources: tuple[SourceRef, ...] = (),
        artifacts: tuple[ArtifactRef, ...] = (),
        memory_citations: tuple[MemoryCitation, ...] = (),
        include_memory_text: bool = True,
        lock_memory: bool = False,
        pinned: EvidenceManifest | None = None,
        project: bool = True,
    ) -> ResolvedEvidence:
        traversal = _Traversal(project=project)
        refs: tuple[EvidenceReference, ...] = (*sources, *artifacts, *memory_citations)
        if lock_memory:
            # Discover immutable entry paths first; lock every owner in one order, then
            # use current reads to observe deactivations committed before these locks.
            observed = await self.resolve(
                connection,
                sources=sources,
                artifacts=artifacts,
                memory_citations=memory_citations,
                project=False,
            )
            owners = tuple(citation for node in observed.manifest.nodes for citation in node.memory_citations)
            atomic_refs = tuple(
                node.artifact
                for node in observed.manifest.nodes
                if node.artifact is not None and node.artifact.family == "atomic-memory"
            )
            await self._lock_memories(connection, owners, atomic_refs)
        for ref in refs:
            await self._visit(connection, ref, traversal, depth=0, direct=True, lock_memory=lock_memory)
        if pinned is not None:
            self._restore_snapshot(traversal, pinned)
        groups = self._groups(traversal)
        grouped_ids = {source_id: group_id for source_id, (group_id, _) in traversal.groups.items()}
        projected: list[ProjectedEvidence] = []
        for node_id, node in sorted(traversal.nodes.items()):
            if node.kind == "unresolved" or node.role in {"unresolved", "lineage_only"}:
                continue
            if node.kind == "memory" and not include_memory_text:
                continue
            roots = root_ids(node_id, traversal.nodes, traversal.edges)
            projected.append(
                ProjectedEvidence(
                    evidence_id=node_id,
                    kind=node.kind,
                    text=traversal.bodies[node_id] if project else "",
                    historical=node.historical,
                    root_group_ids=tuple(sorted({grouped_ids[root] for root in roots})),
                )
            )
        incomplete = any(node.role == "unresolved" for node in traversal.nodes.values())
        projection = EvidenceProjection(evidence=tuple(projected), root_groups=groups, incomplete=incomplete)
        payload = projection.model_dump_json().encode("utf-8")
        if project and (len(projected) > self.limits.max_items or len(payload) > self.limits.max_bytes):
            raise EvidenceResolutionError("evidence_limit_exceeded")
        manifest = EvidenceManifest(
            artifacts=artifacts,
            sources=sources,
            memory_citations=memory_citations,
            nodes=tuple(traversal.nodes[key] for key in sorted(traversal.nodes)),
            edges=tuple(EvidenceEdge(derived_id=left, upstream_id=right) for left, right in sorted(traversal.edges)),
            root_groups=groups,
            projection_digest=content_digest(payload),
            projection_bytes=len(payload),
            incomplete=incomplete,
        )
        if pinned is not None and project and manifest.projection_digest != pinned.projection_digest:
            raise EvidenceResolutionError("evidence_unavailable")
        return ResolvedEvidence(manifest=manifest, projection=projection)

    async def _visit(
        self,
        connection: AsyncConnection,
        ref: EvidenceReference,
        state: _Traversal,
        *,
        depth: int,
        direct: bool = False,
        lock_memory: bool = False,
        history_owner: str | None = None,
    ) -> str:
        if depth > self.limits.max_depth:
            raise EvidenceResolutionError("evidence_limit_exceeded")
        await self._authorize_reference(connection, ref)
        identity = evidence_id(ref)
        try:
            node, body, children = await self._read(
                connection, ref, state, direct=direct, locked=lock_memory, history_owner=history_owner
            )
        except RepositoryNotFoundError as error:
            raise EvidenceResolutionError("reference_not_found" if direct else "evidence_unavailable") from error
        except (InvalidMemoryCitationError, MemoryEntryNotFoundError) as error:
            raise EvidenceResolutionError("invalid_memory_citation") from error
        if identity in state.nodes:
            previous = state.nodes[identity]
            state.nodes[identity] = previous.model_copy(
                update={
                    "memory_citations": unique_references((*previous.memory_citations, *node.memory_citations)),
                }
            )
            return identity
        state.nodes[identity] = node
        state.bodies[identity] = body
        if len(state.nodes) > self.limits.max_nodes:
            raise EvidenceResolutionError("evidence_limit_exceeded")
        for child, owner in children:
            child_id = await self._visit(
                connection, child, state, depth=depth + 1, lock_memory=lock_memory, history_owner=owner
            )
            state.edges.add((identity, child_id))
            if len(state.edges) > self.limits.max_edges:
                raise EvidenceResolutionError("evidence_limit_exceeded")
        return identity

    async def _authorize_reference(self, connection, ref):
        if isinstance(ref, MemoryCitation):
            # Validate exact frozen membership before using mapped Atomic authority.
            await self.memory_reader(connection, ref)
        if self.authorize is not None:
            await self.authorize(ref)

    async def _read(
        self,
        connection: AsyncConnection,
        ref: EvidenceReference,
        state: _Traversal,
        *,
        direct: bool,
        locked: bool,
        history_owner: str | None = None,
    ) -> tuple[EvidenceNode, str, tuple[EvidenceChild, ...]]:
        if isinstance(ref, SourceRef):
            return await self._read_source(connection, ref, state, direct=direct)
        if isinstance(ref, MemoryCitation):
            return await self._read_memory(connection, ref, direct=direct, locked=locked)
        artifact = await self.artifacts.get(connection, self.scope_id, ref)
        digest = content_digest(artifact.model_dump_json().encode())
        if isinstance(artifact, AtomicMemory):
            return await self._read_atomic(connection, ref, artifact, locked=locked, history_owner=history_owner)
        if ref.family == "prompt":
            return (
                EvidenceNode(
                    evidence_id=evidence_id(ref),
                    kind="unresolved",
                    artifact=ref,
                    digest=digest,
                    role="lineage_only",
                ),
                "",
                (),
            )
        if isinstance(artifact, Memory):
            # A frozen collection is provenance metadata. Its exact lineage can reach
            # original Sources only after the authorizer requires Scope read authority.
            return (
                EvidenceNode(
                    evidence_id=evidence_id(ref), kind="unresolved", artifact=ref, digest=digest, role="lineage_only"
                ),
                "",
                _lineage_children(artifact),
            )
        if not isinstance(artifact, Experience) or artifact.lineage.publication_source is not None:
            return (
                EvidenceNode(
                    evidence_id=evidence_id(ref),
                    kind="unresolved",
                    artifact=ref,
                    digest=digest,
                    role="unresolved",
                ),
                "",
                (),
            )
        current = await self.artifacts.latest(connection, self.scope_id, ref.family, ref.artifact_id)
        return (
            EvidenceNode(
                evidence_id=evidence_id(ref),
                kind="experience",
                artifact=ref,
                digest=digest,
                role="derived",
                historical=current.as_ref() != ref,
            ),
            artifact.content.model_dump_json(),
            _lineage_children(artifact),
        )

    async def _read_atomic(self, connection, ref, artifact, *, locked, history_owner):
        current = await self.artifacts.latest(
            connection, self.scope_id, "atomic-memory", ref.artifact_id, for_update=locked
        )
        state = await AtomicMemoryStateRepository().get(connection, self.scope_id, ref.artifact_id, for_update=locked)
        if state.state is not AtomicMemoryStateValue.ACTIVE:
            same_identity_history = history_owner == ref.artifact_id
            frozen_merge_input = (
                state.state is AtomicMemoryStateValue.MERGED
                and state.merged_into_id == history_owner
                and current.as_ref() == ref
            )
            if not same_identity_history and not frozen_merge_input:
                raise EvidenceResolutionError("memory_entry_inactive")
        imported = await read_imported_memory_evidence(connection, self.artifacts, self.scope_id, artifact)
        if imported is not None:
            await self._authorize_reference(connection, imported.anchor)
        digest = content_digest(
            (
                artifact.model_dump_json()
                + current.as_ref().model_dump_json()
                + state.model_dump_json()
                + ("" if imported is None else imported.entry.model_dump_json())
            ).encode()
        )
        return (
            EvidenceNode(
                evidence_id=evidence_id(ref),
                kind="memory",
                artifact=ref,
                digest=digest,
                role="derived",
                historical=current.as_ref() != ref or state.state is not AtomicMemoryStateValue.ACTIVE,
            ),
            artifact.content.model_dump_json(),
            _lineage_children(artifact, lineage=None if imported is None else imported.lineage),
        )

    async def _read_source(
        self,
        connection: AsyncConnection,
        ref: SourceRef,
        state: _Traversal,
        *,
        direct: bool,
    ) -> tuple[EvidenceNode, str, tuple[EvidenceChild, ...]]:
        try:
            stored = await self.generation_sources.require_for_generation(connection, self.scope_id, (ref,))
            source = stored[0].value
            eligible = True
        except SourceNotEligibleError as error:
            if direct:
                raise EvidenceResolutionError("source_not_eligible") from error
            # Internal Sources remain provenance without entering the model projection.
            source = (await self.sources.get(connection, self.scope_id, ref)).value
            eligible = False
        body = source.model_dump_json()
        digest = content_digest(body.encode())
        if eligible and state.project and self.source_projector is not None:
            try:
                body = self.source_projector(source)
            except (InvalidSourceProjectionError, SourceProjectionNotFoundError) as error:
                raise EvidenceResolutionError("evidence_unavailable") from error
        identity = evidence_id(ref)
        if eligible:
            attested = None if self.root_identity is None else self.root_identity(ref, source)
            group_key = reference_key(ref) if attested is None else attested
            state.groups[identity] = ("root_" + content_digest(group_key.encode())[7:31], attested is not None)
        return (
            EvidenceNode(
                evidence_id=identity,
                kind="source",
                source=ref,
                digest=digest,
                role="root" if eligible else "lineage_only",
            ),
            body,
            (),
        )

    async def _read_memory(
        self,
        connection: AsyncConnection,
        ref: MemoryCitation,
        *,
        direct: bool,
        locked: bool,
    ) -> tuple[EvidenceNode, str, tuple[EvidenceChild, ...]]:
        if ref.memory_ref.family != Memory.family:
            raise EvidenceResolutionError("invalid_memory_citation")
        memory = await self.artifacts.get(connection, self.scope_id, ref.memory_ref)
        if not isinstance(memory, Memory):
            raise EvidenceResolutionError("invalid_memory_citation")
        item = next((item for item in memory.content.manifest.entries if item.entry_id == ref.entry_id), None)
        if item is None or item.entry_version_id != ref.entry_version_id:
            raise EvidenceResolutionError("invalid_memory_citation")
        entry = await self.memory_reader(connection, ref)
        mapped_id = legacy_entry_artifact_id(self.scope_id, ref.memory_ref.artifact_id, ref.entry_id)
        current = await self.artifacts.latest(connection, self.scope_id, "atomic-memory", mapped_id, for_update=locked)
        state = await AtomicMemoryStateRepository().get(connection, self.scope_id, mapped_id, for_update=locked)
        if state.state is not AtomicMemoryStateValue.ACTIVE:
            raise EvidenceResolutionError("memory_entry_inactive")
        return (
            EvidenceNode(
                evidence_id=evidence_id(ref),
                kind="memory",
                memory_citations=(ref,),
                digest=content_digest(
                    (entry.entry_content_hash + current.as_ref().model_dump_json() + state.model_dump_json()).encode()
                ),
                role="derived",
                historical=True,
                current_entry_version_id=ref.entry_version_id,
            ),
            entry.model_dump_json(),
            tuple((child, None) for child in (*entry.sources, *entry.artifacts)),
        )

    async def _lock_memories(
        self,
        connection: AsyncConnection,
        citations: tuple[MemoryCitation, ...],
        atomic_refs: tuple[ArtifactRef, ...] = (),
    ) -> None:
        mapped = tuple(
            ArtifactRef(
                family="atomic-memory",
                artifact_id=legacy_entry_artifact_id(self.scope_id, citation.memory_ref.artifact_id, citation.entry_id),
                revision=1,
            )
            for citation in citations
        )
        await self.artifacts.lock_heads(connection, self.scope_id, (*atomic_refs, *mapped))

    @staticmethod
    def _groups(state: _Traversal) -> tuple[RootEvidenceGroup, ...]:
        groups: dict[str, list[SourceRef]] = {}
        attested: dict[str, bool] = {}
        for node_id, (group_id, verified) in state.groups.items():
            ref = state.nodes[node_id].source
            if ref is not None:
                groups.setdefault(group_id, []).append(ref)
                attested[group_id] = verified
        return tuple(
            RootEvidenceGroup(
                group_id=key,
                sources=tuple(sorted(groups[key], key=reference_key)),
                independence="attested" if attested[key] else "unknown",
            )
            for key in sorted(groups)
        )

    @staticmethod
    def _restore_snapshot(state: _Traversal, pinned: EvidenceManifest) -> None:
        if pinned.transform_version != EVIDENCE_TRANSFORM_VERSION:
            raise EvidenceResolutionError("evidence_unavailable")
        before = {node.evidence_id: node for node in pinned.nodes}
        edges = {(edge.derived_id, edge.upstream_id) for edge in pinned.edges}
        if before.keys() != state.nodes.keys() or edges != state.edges:
            raise EvidenceResolutionError("evidence_unavailable")
        for key, node in state.nodes.items():
            old = before[key]
            if node.digest != old.digest or node.memory_citations != old.memory_citations:
                raise EvidenceResolutionError("evidence_unavailable")
            state.nodes[key] = old


def _lineage_children(artifact, *, lineage: ArtifactLineage | None = None) -> tuple[EvidenceChild, ...]:
    lineage = artifact.lineage if lineage is None else lineage
    refs = (*lineage.sources, *lineage.artifacts, *lineage.memory_citations)
    selected = ()
    if isinstance(artifact, AtomicMemory) and artifact.content.creation is not None:
        selected = artifact.content.creation.input_artifact_ids
    return tuple(
        (
            child,
            artifact.artifact_id
            if isinstance(artifact, AtomicMemory)
            and isinstance(child, ArtifactRef)
            and child.family == "atomic-memory"
            and (child.artifact_id == artifact.artifact_id or child.artifact_id in selected)
            else None,
        )
        for child in refs
    )


def evidence_id(ref: EvidenceReference) -> str:
    if isinstance(ref, MemoryCitation):
        identity = f"memory:{ref.memory_ref.artifact_id}:{ref.entry_id}:{ref.entry_version_id}"
    else:
        identity = reference_key(ref)
    return "e_" + content_digest(identity.encode())[7:39]


def root_ids(node_id: str, nodes: dict[str, EvidenceNode], edges: set[tuple[str, str]]) -> set[str]:
    roots: set[str] = set()
    visited: set[str] = set()
    pending = [node_id]
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        if nodes[current].role == "root":
            roots.add(current)
        pending.extend(upstream for derived, upstream in edges if derived == current)
    return roots
