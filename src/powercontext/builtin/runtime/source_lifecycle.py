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

"""Propagate Source availability through an explicit evidence dependency graph.

Callers register captured Sources together with the Memory entries and Topic
Memories derived from them, plus each node's ``all``/``any`` support policy.
Withdrawing or restoring a Source then re-decides every descendant, keeps native
Memory entries and Topic searches consistent with that decision, and retains the
original references for auditing what was relied on before a withdrawal.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import text

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory.models import MemoryCitation, MemoryEntryVersion
from powercontext.builtin.runtime.errors import InvalidRuntimeRequestError
from powercontext.sources.models import SourceRef

SOURCE_LIFECYCLE_TABLE = "pc_source_lifecycle_nodes"

_CREATE_TABLE = f"""
CREATE TABLE IF NOT EXISTS {SOURCE_LIFECYCLE_TABLE} (
    scope_id VARCHAR(256) NOT NULL,
    node_id VARCHAR(256) NOT NULL,
    ordinal INTEGER NOT NULL,
    kind VARCHAR(16) NOT NULL,
    binding TEXT NOT NULL,
    parents TEXT NOT NULL,
    policy VARCHAR(8) NOT NULL,
    revision INTEGER NULL,
    last_sequence INTEGER NULL,
    last_available INTEGER NULL,
    last_reason TEXT NULL,
    active INTEGER NOT NULL,
    PRIMARY KEY (scope_id, node_id)
)
"""

_SOURCE = "source"
_MEMORY = "memory"
_TOPIC = "topic"
_POLICIES = ("all", "any")
_PURPOSES = ("generation", "audit")


class SourceLifecycleError(ValueError):
    """Raised when a lifecycle registration, event or lookup violates the contract."""


class SourceLifecycleEvent(BaseModel):
    """One availability observation for an already registered Source node."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    revision: int
    sequence: int
    available: bool
    reason: str

    @field_validator("node_id", "reason")
    @classmethod
    def require_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("must be a non-empty string")  # noqa: TRY003
        return value

    @field_validator("revision", "sequence")
    @classmethod
    def require_positive(cls, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("must be a positive integer")  # noqa: TRY003
        return value

    @field_validator("available")
    @classmethod
    def require_bool(cls, value: bool) -> bool:
        if not isinstance(value, bool):
            raise ValueError("must be a boolean")  # noqa: TRY003
        return value


@dataclass(frozen=True)
class _Node:
    node_id: str
    ordinal: int
    kind: str
    binding: dict[str, Any]
    parents: tuple[str, ...]
    policy: str
    revision: int | None
    last_sequence: int | None
    last_available: bool | None
    last_reason: str | None
    active: bool


class ScopedSourceLifecycle:
    """Manage the dependency graph and availability of one Scope."""

    def __init__(self, application: SourceLifecycleApplication, scope_id: str) -> None:
        self._application = application
        self._runtime = application._runtime
        self.scope_id = scope_id

    async def register_source(self, node_id: str, reference: SourceRef, *, revision: int) -> None:
        """Register a captured Source at one lifecycle revision."""

        _require_node_id(node_id)
        _require_positive_int("revision", revision)
        record = await _readable(
            self._runtime, self.scope_id, lambda: self._runtime.records.for_scope(self.scope_id).get_source(
                reference.source_type, reference.source_id
            )
        )
        if record is None:
            raise SourceLifecycleError("registered Source is not readable")
        await self._register(
            node_id=node_id,
            kind=_SOURCE,
            binding=reference.model_dump(mode="json"),
            parents=(),
            policy="all",
            revision=revision,
            active=True,
        )
        await self._reconcile()

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        *,
        parents: tuple[str, ...],
        policy: str,
    ) -> None:
        """Register one Memory entry as derived from already registered parents."""

        _require_node_id(node_id)
        _require_policy(policy)
        record = await _readable(
            self._runtime,
            self.scope_id,
            lambda: self._runtime.memory.for_scope(self.scope_id).get(
                _get_memory_request(citation)
            ),
        )
        if record is None:
            raise SourceLifecycleError("registered Memory entry is not readable")
        await self._register(
            node_id=node_id,
            kind=_MEMORY,
            binding=citation.model_dump(mode="json"),
            parents=parents,
            policy=policy,
            revision=None,
            active=None,
        )
        await self._reconcile()

    async def register_topic(
        self,
        node_id: str,
        artifact: ArtifactRef,
        *,
        parents: tuple[str, ...],
        policy: str,
    ) -> None:
        """Register one Topic Memory artifact as derived from registered parents."""

        _require_node_id(node_id)
        _require_policy(policy)
        if artifact.family != "topic-memory":
            raise SourceLifecycleError("registered Topic artifact must belong to topic-memory")
        record = await _readable(
            self._runtime,
            self.scope_id,
            lambda: self._runtime.records.for_scope(self.scope_id).get_artifact_revision(
                artifact.family, artifact.artifact_id, artifact.revision
            ),
        )
        if record is None:
            raise SourceLifecycleError("registered Topic artifact is not readable")
        await self._register(
            node_id=node_id,
            kind=_TOPIC,
            binding=artifact.model_dump(mode="json"),
            parents=parents,
            policy=policy,
            revision=None,
            active=None,
        )
        await self._reconcile()

    async def apply(self, event: SourceLifecycleEvent, /) -> list[str]:
        """Apply one Source availability event and return the sorted active node ids."""

        if not isinstance(event, SourceLifecycleEvent):
            raise SourceLifecycleError("apply requires a SourceLifecycleEvent")
        nodes = await self._load()
        node = next((item for item in nodes if item.node_id == event.node_id), None)
        if node is None or node.kind != _SOURCE:
            raise SourceLifecycleError(f"event targets an unregistered Source {event.node_id!r}")
        if node.revision != event.revision:
            raise SourceLifecycleError(
                f"event revision {event.revision} does not match registered revision {node.revision}"
            )
        if node.last_sequence is not None:
            if event.sequence < node.last_sequence:
                raise SourceLifecycleError("event sequence regressed")
            if event.sequence == node.last_sequence:
                if node.last_available == event.available and node.last_reason == event.reason:
                    # Idempotent replay: still reconcile so a retry finishes any
                    # propagation a previous failure interrupted.
                    await self._reconcile()
                    return sorted(item.node_id for item in await self._load() if item.active)
                raise SourceLifecycleError("event sequence reused with different content")

        await self._write_source_state(event)
        await self._recompute()
        await self._reconcile()
        return sorted(item.node_id for item in await self._load() if item.active)

    async def resolve(self, node_id: str, *, purpose: str) -> SourceRef | MemoryCitation | ArtifactRef:
        """Return the original reference when a node may be used for ``purpose``."""

        _require_node_id(node_id)
        if purpose not in _PURPOSES:
            raise SourceLifecycleError(f"unknown resolve purpose {purpose!r}")
        nodes = await self._load()
        node = next((item for item in nodes if item.node_id == node_id), None)
        if node is None:
            raise SourceLifecycleError(f"unknown lifecycle node {node_id!r}")
        if purpose == "generation" and not node.active:
            raise SourceLifecycleError(f"node {node_id!r} is not available for generation")
        return _decode_binding(node)

    # -- storage ---------------------------------------------------------

    async def _register(
        self,
        *,
        node_id: str,
        kind: str,
        binding: dict[str, Any],
        parents: tuple[str, ...],
        policy: str,
        revision: int | None,
        active: bool | None,
    ) -> None:
        if kind != _SOURCE:
            _require_parents(node_id, parents)
        existing = await self._load()
        if any(item.node_id == node_id for item in existing):
            current = next(item for item in existing if item.node_id == node_id)
            if (
                current.kind == kind
                and current.binding == binding
                and current.parents == tuple(parents)
                and current.policy == policy
                and current.revision == revision
            ):
                return
            raise SourceLifecycleError(f"node {node_id!r} is already registered with a different binding")
        known = {item.node_id: item for item in existing}
        if kind != _SOURCE:
            for parent in parents:
                if parent not in known:
                    raise SourceLifecycleError(f"parent {parent!r} must be registered before {node_id!r}")
        computed = True if active is None else active
        if kind != _SOURCE:
            computed = _decide(parents, policy, known)
        async with self._database().transaction() as connection:
            await connection.execute(text(_CREATE_TABLE))
            await connection.execute(
                text(
                    f"INSERT INTO {SOURCE_LIFECYCLE_TABLE} "
                    "(scope_id, node_id, ordinal, kind, binding, parents, policy, revision, "
                    "last_sequence, last_available, last_reason, active) "
                    "VALUES (:scope_id, :node_id, :ordinal, :kind, :binding, :parents, :policy, :revision, "
                    "NULL, NULL, NULL, :active)"
                ),
                {
                    "scope_id": self.scope_id,
                    "node_id": node_id,
                    "ordinal": len(existing),
                    "kind": kind,
                    "binding": json.dumps(binding, sort_keys=True),
                    "parents": json.dumps(list(parents)),
                    "policy": policy,
                    "revision": revision,
                    "active": int(computed),
                },
            )

    async def _write_source_state(self, event: SourceLifecycleEvent) -> None:
        async with self._database().transaction() as connection:
            await connection.execute(text(_CREATE_TABLE))
            await connection.execute(
                text(
                    f"UPDATE {SOURCE_LIFECYCLE_TABLE} SET last_sequence = :sequence, last_available = :available, "
                    "last_reason = :reason, active = :active WHERE scope_id = :scope_id AND node_id = :node_id"
                ),
                {
                    "scope_id": self.scope_id,
                    "node_id": event.node_id,
                    "sequence": event.sequence,
                    "available": int(event.available),
                    "reason": event.reason,
                    "active": int(event.available),
                },
            )

    async def _load(self) -> list[_Node]:
        async with self._database().transaction() as connection:
            await connection.execute(text(_CREATE_TABLE))
            rows = (
                (
                    await connection.execute(
                        text(f"SELECT * FROM {SOURCE_LIFECYCLE_TABLE} WHERE scope_id = :scope_id ORDER BY ordinal"),
                        {"scope_id": self.scope_id},
                    )
                )
                .mappings()
                .all()
            )
        return [_node_from_row(dict(row)) for row in rows]

    async def _recompute(self) -> None:
        nodes = await self._load()
        states: dict[str, bool] = {}
        updates: list[tuple[str, bool]] = []
        for node in nodes:
            if node.kind == _SOURCE:
                states[node.node_id] = node.active
                continue
            decided = _decide(node.parents, node.policy, states)
            states[node.node_id] = decided
            if decided != node.active:
                updates.append((node.node_id, decided))
        if not updates:
            return
        async with self._database().transaction() as connection:
            for node_id, decided in updates:
                await connection.execute(
                    text(
                        f"UPDATE {SOURCE_LIFECYCLE_TABLE} SET active = :active "
                        "WHERE scope_id = :scope_id AND node_id = :node_id"
                    ),
                    {"scope_id": self.scope_id, "node_id": node_id, "active": int(decided)},
                )

    async def _reconcile(self) -> None:
        """Bring native Memory entry states in line with the computed graph."""

        nodes = await self._load()
        wanted = {node.node_id: node for node in nodes if node.kind == _MEMORY}
        if not wanted:
            return
        async with self._runtime._context(self.scope_id) as context, self._runtime._locked(self.scope_id):
            service = context.artifacts.memory
            artifact_id = context.artifacts.memory_artifact_id
            current = await _head_or_none(service, artifact_id)
            if current is None:
                return
            entries = await service.entries(current)
            states = _manifest_states(current, entries)
            deactivate: list[MemoryEntryVersion] = []
            restore: list[MemoryEntryVersion] = []
            for entry in entries:
                node = next(
                    (item for item in wanted.values() if _entry_id(item) == entry.entry_id),
                    None,
                )
                if node is None:
                    continue
                state = states.get(entry.entry_id)
                if node.active and state == "inactive":
                    restore.append(entry)
                elif not node.active and state == "active":
                    deactivate.append(entry)
            if deactivate:
                current = await service.forget(current, entries=tuple(deactivate), reason="source-lifecycle")
            if restore:
                await service.reactivate(current, entries=tuple(restore), reason="source-lifecycle")

    def _database(self):
        service = self._runtime._records()
        database = getattr(service, "_database", None)
        if database is None:
            raise SourceLifecycleError("lifecycle persistence requires a relational Record service")
        return database


class SourceLifecycleApplication:
    """Select the lifecycle service for one Scope and expose scope suppression."""

    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycle:
        return ScopedSourceLifecycle(self, scope_id)

    async def suppressed_artifacts(self, scope_id: str, /) -> frozenset[str]:
        """Return Topic artifact ids whose evidence no longer supports them."""

        service = ScopedSourceLifecycle(self, scope_id)
        nodes = await service._load()
        return frozenset(
            str(node.binding.get("artifact_id"))
            for node in nodes
            if node.kind == _TOPIC and not node.active and node.binding.get("artifact_id")
        )


def _entry_id(node: _Node) -> str | None:
    return node.binding.get("entry_id")


def _decide(parents: tuple[str, ...], policy: str, states: dict[str, Any] | dict[str, bool]) -> bool:
    def active(parent: str) -> bool:
        value = states[parent]
        return bool(value.active if isinstance(value, _Node) else value)

    support = [active(parent) for parent in parents]
    return all(support) if policy == "all" else any(support)


def _node_from_row(row: dict[str, Any]) -> _Node:
    return _Node(
        node_id=str(row["node_id"]),
        ordinal=int(row["ordinal"]),
        kind=str(row["kind"]),
        binding=json.loads(row["binding"]),
        parents=tuple(json.loads(row["parents"])),
        policy=str(row["policy"]),
        revision=None if row["revision"] is None else int(row["revision"]),
        last_sequence=None if row["last_sequence"] is None else int(row["last_sequence"]),
        last_available=None if row["last_available"] is None else bool(row["last_available"]),
        last_reason=row["last_reason"],
        active=bool(row["active"]),
    )


def _decode_binding(node: _Node) -> SourceRef | MemoryCitation | ArtifactRef:
    if node.kind == _SOURCE:
        return SourceRef.model_validate(node.binding)
    if node.kind == _MEMORY:
        return MemoryCitation.model_validate(node.binding)
    return ArtifactRef.model_validate(node.binding)


def _manifest_states(memory: Any, entries: Any) -> dict[str, str]:
    by_version = {item.entry_version_id: item.state for item in memory.content.manifest.entries}
    states: dict[str, str] = {}
    for entry in entries:
        state = by_version.get(entry.entry_version_id)
        if state is not None:
            states[entry.entry_id] = state
    return states


async def _head_or_none(service: Any, artifact_id: str) -> Any:
    from powercontext.errors import ArtifactNotFoundError

    try:
        return await service.head(artifact_id)
    except ArtifactNotFoundError:
        return None


async def _readable(runtime: Any, scope_id: str, reader: Any) -> Any:
    try:
        return await reader()
    except SourceLifecycleError:
        raise
    except Exception as error:  # noqa: BLE001 - any unreadable binding is a registration error
        raise SourceLifecycleError(f"registered reference is not readable: {error}") from error


def _get_memory_request(citation: MemoryCitation) -> Any:
    from powercontext.builtin.runtime.models import GetMemoryEntryRequest

    try:
        return GetMemoryEntryRequest(citation=citation)
    except InvalidRuntimeRequestError as error:
        raise SourceLifecycleError("citation cannot address one Memory entry") from error


def _require_node_id(node_id: object) -> None:
    if not isinstance(node_id, str) or not node_id.strip():
        raise SourceLifecycleError("node_id must be a non-empty string")


def _require_positive_int(field: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SourceLifecycleError(f"{field} must be a positive integer")


def _require_policy(policy: object) -> None:
    if policy not in _POLICIES:
        raise SourceLifecycleError(f"policy must be one of {_POLICIES}")


def _require_parents(node_id: str, parents: tuple[str, ...]) -> None:
    if not isinstance(parents, tuple) or not parents:
        raise SourceLifecycleError("derived nodes require at least one parent")
    if node_id in parents:
        raise SourceLifecycleError("a node cannot depend on itself")
    if len(set(parents)) != len(parents):
        raise SourceLifecycleError("parents must be unique")
    for parent in parents:
        if not isinstance(parent, str) or not parent.strip():
            raise SourceLifecycleError("parents must be non-empty node ids")


__all__ = ["SourceLifecycleApplication", "SourceLifecycleError", "SourceLifecycleEvent"]
