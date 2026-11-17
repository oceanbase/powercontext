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

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemory, AtomicMemoryStateValue
from powercontext.builtin.artifacts.experience import Experience
from powercontext.builtin.artifacts.handoff.models import (
    Handoff,
    HandoffArtifactCitation,
    HandoffSourceCitation,
)
from powercontext.builtin.artifacts.profile.models import Profile
from powercontext.builtin.artifacts.prompt.models import Prompt
from powercontext.builtin.artifacts.skill import Skill
from powercontext.builtin.artifacts.topic_memory.models import TopicMemory
from powercontext.builtin.evidence.models import (
    EVIDENCE_TRANSFORM_VERSION,
    QUALIFIED_EVIDENCE_TRANSFORM_VERSION,
    EvidenceEdge,
    EvidenceKind,
    EvidenceLimits,
    EvidenceManifest,
    EvidenceNode,
    EvidenceProjection,
    EvidenceResolutionError,
    EvidenceRole,
    ProjectedEvidence,
    QualifiedOrigin,
    ResolvedEvidence,
    RootEvidenceGroup,
    content_digest,
    reference_key,
)
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.atomic_memory import AtomicMemoryStateRepository
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.generation_sources import GenerationSourceAccess
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.source_eligibility import SourceNotEligibleError
from powercontext.errors import InvalidSourceProjectionError, SourceProjectionNotFoundError
from powercontext.sources import Source, SourceRef

EvidenceReference = ArtifactRef | SourceRef
EvidenceChild = tuple[EvidenceReference, str | None]
EvidenceAuthorizer = Callable[[EvidenceReference], Awaitable[None]]
AuthorizationContext = Callable[[], AbstractAsyncContextManager[None]]
ScopedEvidenceAuthorizer = Callable[[str, EvidenceReference], Awaitable[None]]
ScopedEvidenceBatchAuthorizer = Callable[[str, tuple[EvidenceReference, ...], AsyncConnection], Awaitable[None]]
RootIdentityResolver = Callable[[SourceRef, Source], str | None]


@dataclass
class _Traversal:
    project: bool = True
    target_ref: ArtifactRef | None = None
    prompt_target: ArtifactRef | None = None
    catalog_target: ArtifactRef | None = None
    nodes: dict[str, EvidenceNode] = field(default_factory=dict)
    bodies: dict[str, str] = field(default_factory=dict)
    edges: set[tuple[str, str]] = field(default_factory=set)
    groups: dict[str, tuple[str, bool, str]] = field(default_factory=dict)
    # Which Scope owns each node. Local nodes resolve in the run Scope; a qualified
    # selection resolves in the Scope that actually owns it.
    origins: dict[str, str] = field(default_factory=dict)


class EvidenceResolver:
    def __init__(
        self,
        *,
        scope_id: str,
        sources: SourceRepository,
        artifacts: ArtifactRepository,
        authorize: EvidenceAuthorizer | None = None,
        authorize_origin: ScopedEvidenceAuthorizer | None = None,
        root_identity: RootIdentityResolver | None = None,
        source_projector: Callable[[Source], str] | None = None,
        limits: EvidenceLimits | None = None,
    ) -> None:
        self.scope_id = scope_id
        self.sources = sources
        self.generation_sources = GenerationSourceAccess(sources)
        self.artifacts = artifacts
        self.authorize = authorize
        self.authorize_origin = authorize_origin
        self.root_identity = root_identity
        self.source_projector = source_projector
        self.limits = EvidenceLimits() if limits is None else limits

    async def validate(
        self,
        connection: AsyncConnection,
        *,
        sources: tuple[SourceRef, ...] = (),
        artifacts: tuple[ArtifactRef, ...] = (),
        qualified: tuple[QualifiedOrigin, ...] = (),
    ) -> tuple[SourceRef, ...]:
        """Validate lineage and return eligible Source roots of directly selected Memory evidence.

        ``qualified`` inputs are resolved in the Scope that owns them, so an equal local
        identity never stands in for another Scope's material.
        """

        refs: tuple[EvidenceReference, ...] = (*sources, *artifacts)
        state = await self._validate_lineage(connection, refs)
        atomic_refs = tuple(
            node.artifact
            for node in state.nodes.values()
            if node.artifact is not None and node.artifact.family == "atomic-memory"
        )
        if atomic_refs:
            # Immutable lineage discovers the owners; current reads under ordered
            # head locks serialize approval against entry deactivation.
            await self.artifacts.lock_heads(connection, self.scope_id, atomic_refs)
            state = await self._validate_lineage(connection, refs, locked=True)
        for selection in qualified:
            await self._validate_qualified(connection, selection)
        roots: set[str] = set()
        for origin in (ref for ref in artifacts if ref.family == "atomic-memory"):
            roots.update(root_ids(evidence_id(origin, self.scope_id), state.nodes, state.edges))
        return tuple(source for key in sorted(roots) if (source := state.nodes[key].source) is not None)

    async def _validate_qualified(self, connection: AsyncConnection, selection: QualifiedOrigin) -> None:
        """Read one qualified input in its owning Scope and verify the recorded digest."""

        reference = selection.reference
        if isinstance(reference, SourceRef):
            stored = await self.generation_sources.require_for_generation(connection, selection.scope_id, (reference,))
            digest = content_digest(stored[0].value.model_dump_json().encode())
        else:
            artifact = await self.artifacts.get(connection, selection.scope_id, reference)
            digest = content_digest(artifact.model_dump_json().encode())
        if digest != selection.digest:
            # The origin's content changed since the snapshot pinned it.
            raise EvidenceResolutionError("evidence_changed")

    async def _validate_lineage(
        self,
        connection: AsyncConnection,
        refs: tuple[EvidenceReference, ...],
        *,
        locked: bool = False,
    ) -> _Traversal:
        state = _Traversal(project=False)
        pending: deque[tuple[EvidenceReference, str, bool, str | None]] = deque(
            (ref, self.scope_id, True, None) for ref in refs
        )
        visited: set[tuple[str, str, str | None]] = set()
        while pending:
            ref, origin_scope_id, direct, history_owner = pending.popleft()
            key = (origin_scope_id, reference_key(ref), history_owner)
            if key in visited:
                continue
            visited.add(key)
            await self._authorize_reference(connection, ref, origin_scope_id)
            try:
                if isinstance(ref, ArtifactRef):
                    node, children = await self._read_review_artifact(
                        connection,
                        ref,
                        direct=direct,
                        locked=locked,
                        history_owner=history_owner,
                        origin_scope_id=origin_scope_id,
                    )
                else:
                    node, _, children = await self._read(
                        connection,
                        ref,
                        state,
                        direct=direct,
                        locked=locked,
                        history_owner=history_owner,
                        origin_scope_id=origin_scope_id,
                    )
            except RepositoryNotFoundError as error:
                raise EvidenceResolutionError("reference_not_found" if direct else "evidence_unavailable") from error
            state.nodes.setdefault(node.evidence_id, node)
            state.origins.setdefault(node.evidence_id, origin_scope_id)
            state.edges.update((node.evidence_id, evidence_id(child, origin_scope_id)) for child, _ in children)
            pending.extend((child, origin_scope_id, False, owner) for child, owner in children)
        return state

    async def _read_review_artifact(
        self,
        connection: AsyncConnection,
        ref: ArtifactRef,
        *,
        direct: bool = True,
        locked: bool = False,
        history_owner: str | None = None,
        origin_scope_id: str | None = None,
    ) -> tuple[EvidenceNode, tuple[EvidenceChild, ...]]:
        """Follow Review lineage independently of Dream's supported input Families."""

        scope_id = self.scope_id if origin_scope_id is None else origin_scope_id
        artifact = await self.artifacts.get(connection, scope_id, ref)
        if isinstance(artifact, AtomicMemory):
            node, _, children = await self._read_atomic(
                connection, ref, artifact, locked=locked, history_owner=history_owner, origin_scope_id=scope_id
            )
            return node, children
        children: tuple[EvidenceChild, ...] = ()
        if ref.family != "prompt" and artifact.lineage.publication_source is None:
            children = _artifact_children(artifact, ref, _Traversal(project=False))
        return (
            EvidenceNode(
                evidence_id=evidence_id(ref, scope_id),
                kind="unresolved",
                artifact=ref,
                digest=content_digest(artifact.model_dump_json().encode()),
                origin_scope_id=scope_id,
                role="lineage_only",
            ),
            children,
        )

    async def resolve(  # noqa: C901 - one ordered resolve seam
        self,
        connection: AsyncConnection,
        *,
        sources: tuple[SourceRef, ...] = (),
        artifacts: tuple[ArtifactRef, ...] = (),
        qualified: tuple[QualifiedOrigin, ...] = (),
        target: ArtifactRef | None = None,
        include_memory_text: bool = True,
        lock_memory: bool = False,
        pinned: EvidenceManifest | None = None,
        project: bool = True,
        expose_prompt_target: bool = False,
        expose_catalog_target: bool = False,
    ) -> ResolvedEvidence:
        traversal = _Traversal(
            project=project,
            target_ref=target,
            prompt_target=target if expose_prompt_target and isinstance(target, ArtifactRef) else None,
            catalog_target=target if expose_catalog_target and isinstance(target, ArtifactRef) else None,
        )
        refs: tuple[EvidenceReference, ...] = (*sources, *artifacts)
        if lock_memory:
            # Discover immutable entry paths first; lock every owner in one order, then
            # use current reads to observe deactivations committed before these locks.
            observed = await self.resolve(
                connection,
                sources=sources,
                artifacts=artifacts,
                qualified=qualified,
                target=target,
                project=False,
                expose_prompt_target=expose_prompt_target,
                expose_catalog_target=expose_catalog_target,
            )
            atomic_refs = tuple(
                node.artifact
                for node in observed.manifest.nodes
                if node.artifact is not None and node.artifact.family == "atomic-memory"
            )
            await self.artifacts.lock_heads(connection, self.scope_id, atomic_refs)
        for ref in refs:
            await self._visit(connection, ref, traversal, depth=0, direct=True, lock_memory=lock_memory)
        for selection in qualified:
            await self._visit(
                connection,
                selection.reference,
                traversal,
                depth=0,
                direct=True,
                lock_memory=lock_memory,
                origin_scope_id=selection.scope_id,
            )
        if target is not None and (target_id := evidence_id(target, self.scope_id)) in traversal.nodes:
            traversal.nodes[target_id] = traversal.nodes[target_id].model_copy(update={"role": "target"})
        if pinned is not None:
            self._restore_snapshot(traversal, pinned)
        groups = self._groups(traversal)
        grouped_ids = {node_id: group[0] for node_id, group in traversal.groups.items()}
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
            transform_version=QUALIFIED_EVIDENCE_TRANSFORM_VERSION,
            artifacts=artifacts,
            sources=sources,
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
        origin_scope_id: str | None = None,
    ) -> str:
        if depth > self.limits.max_depth:
            raise EvidenceResolutionError("evidence_limit_exceeded")
        scope_id = self.scope_id if origin_scope_id is None else origin_scope_id
        await self._authorize_reference(connection, ref, scope_id)
        identity = evidence_id(ref, scope_id)
        try:
            node, body, children = await self._read(
                connection,
                ref,
                state,
                direct=direct,
                locked=lock_memory,
                history_owner=history_owner,
                origin_scope_id=scope_id,
            )
        except RepositoryNotFoundError as error:
            raise EvidenceResolutionError("reference_not_found" if direct else "evidence_unavailable") from error
        if identity in state.nodes:
            return identity
        state.nodes[identity] = node
        state.bodies[identity] = body
        state.origins[identity] = scope_id
        if len(state.nodes) > self.limits.max_nodes:
            raise EvidenceResolutionError("evidence_limit_exceeded")
        for child, owner in children:
            child_id = await self._visit(
                connection,
                child,
                state,
                depth=depth + 1,
                lock_memory=lock_memory,
                history_owner=owner,
                origin_scope_id=scope_id,
            )
            state.edges.add((identity, child_id))
            if len(state.edges) > self.limits.max_edges:
                raise EvidenceResolutionError("evidence_limit_exceeded")
        return identity

    async def _authorize_reference(self, connection, ref, origin_scope_id: str | None = None):
        scope_id = self.scope_id if origin_scope_id is None else origin_scope_id
        if scope_id != self.scope_id and self.authorize_origin is not None:
            # Another Scope's material is authorized at the Scope that owns it.
            await self.authorize_origin(scope_id, ref)
            return
        if self.authorize is not None:
            await self.authorize(ref)

    async def _read(  # noqa: C901 - explicit projection contracts for each Family
        self,
        connection: AsyncConnection,
        ref: EvidenceReference,
        state: _Traversal,
        *,
        direct: bool,
        locked: bool,
        history_owner: str | None = None,
        origin_scope_id: str | None = None,
    ) -> tuple[EvidenceNode, str, tuple[EvidenceChild, ...]]:
        # Every node is constructed with the Scope that owns it, so an equal reference in
        # two Scopes never collapses into one identity.
        scope_id = self.scope_id if origin_scope_id is None else origin_scope_id
        if isinstance(ref, SourceRef):
            return await self._read_source(connection, ref, state, direct=direct, origin_scope_id=scope_id)
        artifact = await self.artifacts.get(connection, scope_id, ref)
        digest = content_digest(artifact.model_dump_json().encode())

        def node(kind: EvidenceKind, role: EvidenceRole, *, historical: bool = False) -> EvidenceNode:
            return EvidenceNode(
                evidence_id=evidence_id(ref, scope_id),
                kind=kind,
                artifact=ref,
                digest=digest,
                origin_scope_id=scope_id,
                role=role,
                historical=historical,
            )

        if isinstance(artifact, AtomicMemory):
            node_, body, children = await self._read_atomic(
                connection, ref, artifact, locked=locked, history_owner=history_owner, origin_scope_id=scope_id
            )
            if ref == state.target_ref:
                return node_.model_copy(update={"role": "target"}), body, ()
            return node_, body, children
        if ref == state.catalog_target:
            return node("catalog_target", "target"), artifact.content.model_dump_json(), ()
        if isinstance(artifact, Skill):
            return (
                node("skill", "derived"),
                artifact.content.model_dump_json(),
                _artifact_children(artifact, ref, state),
            )
        if isinstance(artifact, Profile):
            return node("profile", "derived"), artifact.content.content, _artifact_children(artifact, ref, state)
        if isinstance(artifact, TopicMemory):
            return (
                node("topic_memory", "derived"),
                artifact.content.model_dump_json(),
                _artifact_children(artifact, ref, state),
            )
        if isinstance(artifact, Handoff):
            return (
                node("handoff", "derived"),
                artifact.content.model_dump_json(),
                _artifact_children(artifact, ref, state),
            )
        if ref.family == "prompt":
            if isinstance(artifact, Prompt) and ref == state.prompt_target:
                return node("prompt", "target"), artifact.content.model_dump_json(), ()
            return node("unresolved", "lineage_only"), "", _artifact_children(artifact, ref, state)
        if not isinstance(artifact, Experience) or artifact.lineage.publication_source is not None:
            # A published copy is delivered material, never an independent observation, in
            # whichever Scope it is stored. A qualified external Experience is real support.
            return node("unresolved", "unresolved"), "", ()
        current = await self.artifacts.latest(connection, scope_id, ref.family, ref.artifact_id)
        return (
            node("experience", "derived", historical=current.as_ref() != ref),
            artifact.content.model_dump_json(),
            _artifact_children(artifact, ref, state),
        )

    async def _read_atomic(self, connection, ref, artifact, *, locked, history_owner, origin_scope_id=None):
        if origin_scope_id is not None and origin_scope_id != self.scope_id:
            # Atomic Memory's entry state and merge targets are owned by their Scope.
            raise EvidenceResolutionError("unsupported_origin")
        current = await self.artifacts.latest(
            connection,
            self.scope_id,
            "atomic-memory",
            ref.artifact_id,
            for_update=locked,
            known=None if locked else artifact,
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
        digest = content_digest(
            (artifact.model_dump_json() + current.as_ref().model_dump_json() + state.model_dump_json()).encode()
        )
        return (
            EvidenceNode(
                evidence_id=evidence_id(ref, self.scope_id),
                kind="memory",
                artifact=ref,
                digest=digest,
                origin_scope_id=self.scope_id,
                role="derived",
                historical=current.as_ref() != ref or state.state is not AtomicMemoryStateValue.ACTIVE,
            ),
            artifact.content.model_dump_json(),
            _lineage_children(artifact),
        )

    async def _read_source(
        self,
        connection: AsyncConnection,
        ref: SourceRef,
        state: _Traversal,
        *,
        direct: bool,
        origin_scope_id: str | None = None,
    ) -> tuple[EvidenceNode, str, tuple[EvidenceChild, ...]]:
        scope_id = self.scope_id if origin_scope_id is None else origin_scope_id
        try:
            stored = await self.generation_sources.require_for_generation(connection, scope_id, (ref,))
            source = stored[0].value
            eligible = True
        except SourceNotEligibleError as error:
            if direct:
                raise EvidenceResolutionError("source_not_eligible") from error
            # Internal Sources remain provenance without entering the model projection.
            source = (await self.sources.get(connection, scope_id, ref)).value
            eligible = False
        body = source.model_dump_json()
        digest = content_digest(body.encode())
        if eligible and state.project and self.source_projector is not None:
            try:
                body = self.source_projector(source)
            except (InvalidSourceProjectionError, SourceProjectionNotFoundError) as error:
                raise EvidenceResolutionError("evidence_unavailable") from error
        identity = evidence_id(ref, scope_id)
        if eligible:
            attested = None if self.root_identity is None else self.root_identity(ref, source)
            # The owning Scope is part of the group key, so equal local Source ids in two
            # Scopes are two root groups. Equality of ids or digests is not provenance.
            key = ref.model_dump_json(exclude={"locator"}) if attested is None else attested
            group_key = key if scope_id == self.scope_id else f"{scope_id}\x00{key}"
            state.groups[identity] = (
                "root_" + content_digest(group_key.encode())[7:31],
                attested is not None,
                scope_id,
            )
        return (
            EvidenceNode(
                evidence_id=identity,
                kind="source",
                source=ref,
                digest=digest,
                origin_scope_id=scope_id,
                role="root" if eligible else "lineage_only",
            ),
            body,
            (),
        )

    @staticmethod
    def _groups(state: _Traversal) -> tuple[RootEvidenceGroup, ...]:
        groups: dict[str, list[SourceRef]] = {}
        attested: dict[str, bool] = {}
        scopes: dict[str, str] = {}
        for node_id, (group_id, verified, scope_id) in state.groups.items():
            ref = state.nodes[node_id].source
            if ref is not None:
                groups.setdefault(group_id, []).append(ref)
                attested[group_id] = verified
                scopes[group_id] = scope_id
        return tuple(
            RootEvidenceGroup(
                group_id=key,
                sources=tuple(sorted(groups[key], key=reference_key)),
                scope_id=scopes[key],
                independence="attested" if attested[key] else "unknown",
            )
            for key in sorted(groups)
        )

    @staticmethod
    def _restore_snapshot(state: _Traversal, pinned: EvidenceManifest) -> None:
        """Re-check a replayed run against the manifest it pinned.

        A v1 manifest recorded unqualified node ids. Comparing those ids to today's
        qualified ones would refuse every historical replay for a reason that has nothing
        to do with the evidence, so a v1 pin is compared on the digests of the nodes it
        recorded, and its own identities are restored for readback. A v2 pin is compared
        exactly on ids, edges and digests. Neither path skips the freshness check: the
        digests are always compared.
        """

        version = pinned.transform_version
        if version not in {EVIDENCE_TRANSFORM_VERSION, QUALIFIED_EVIDENCE_TRANSFORM_VERSION}:
            raise EvidenceResolutionError("evidence_unavailable")
        before = {node.evidence_id: node for node in pinned.nodes}
        if version == EVIDENCE_TRANSFORM_VERSION:
            for key, node in state.nodes.items():
                old = before.get(key)
                if old is not None and node.digest != old.digest:
                    raise EvidenceResolutionError("evidence_unavailable")
            return
        edges = {(edge.derived_id, edge.upstream_id) for edge in pinned.edges}
        if before.keys() != state.nodes.keys() or edges != state.edges:
            raise EvidenceResolutionError("evidence_unavailable")
        for key, node in state.nodes.items():
            old = before[key]
            if node.digest != old.digest:
                raise EvidenceResolutionError("evidence_unavailable")
            state.nodes[key] = old


def _lineage_children(artifact) -> tuple[EvidenceChild, ...]:
    lineage = artifact.lineage
    refs = (*lineage.sources, *lineage.artifacts)
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


def evidence_id(ref: EvidenceReference, origin_scope_id: str) -> str:
    """One node identity: the owning Scope plus the exact reference.

    The Scope is part of the digest, not a prefix, so two Scopes' equal identities can
    never produce the same id and the encoding stays a fixed-width hash.
    """

    return "e_" + content_digest((origin_scope_id + "\x00" + reference_key(ref)).encode())[7:39]


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


def _artifact_children(artifact: object, ref: ArtifactRef, state: _Traversal) -> tuple[EvidenceChild, ...]:
    """Return support dependencies while exempting the exact Dream target."""

    if ref == state.target_ref and ref.family != "experience":
        return ()
    lineage = getattr(artifact, "lineage", None)
    children: list[EvidenceReference] = []
    if lineage is not None:
        children.extend(lineage.sources)
        children.extend(lineage.artifacts)
    if isinstance(artifact, Handoff):
        statements = (
            *artifact.content.state,
            *((artifact.content.next_action,) if artifact.content.next_action else ()),
        )
        citations = [citation for statement in statements for citation in statement.citations]
        citations.extend(omission.citation for omission in artifact.content.omissions if omission.citation is not None)
        for citation in citations:
            if isinstance(citation, HandoffSourceCitation):
                children.append(citation.source_ref)
            elif isinstance(citation, HandoffArtifactCitation):
                children.append(citation.artifact_ref)
    return tuple(
        {
            (reference_key(child), owner): (child, owner)
            for child, owner in (*_lineage_children(artifact), *((child, None) for child in children))
        }.values()
    )
