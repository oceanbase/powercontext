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

"""Application service for Scope ownership, organization, and binding."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from collections import deque
from collections.abc import Callable, Sequence

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.scope.errors import (
    ScopeBindingNotFoundError,
    ScopeIdempotencyConflictError,
    ScopeNotFoundError,
    ScopeRelationshipError,
    ScopeVersionConflictError,
)
from powercontext.builtin.scope.models import (
    ScopeBinding,
    ScopeBindingKey,
    ScopeDescriptor,
    ScopeDraft,
    ScopeMutation,
    ScopeSelection,
)
from powercontext.builtin.scope.repository import ScopeRepository

ScopeIdFactory = Callable[[], str]
_CROCKFORD = "0123456789abcdefghjkmnpqrstvwxyz"
_DEFAULT_IDEMPOTENCY_KEY = "powercontext.default-scope.v1"


class ScopeApplication:
    def __init__(
        self,
        database: AsyncDatabase,
        *,
        repository: ScopeRepository | None = None,
        id_factory: ScopeIdFactory | None = None,
    ) -> None:
        self._database = database
        self._repository = ScopeRepository() if repository is None else repository
        self._id_factory = generate_scope_id if id_factory is None else id_factory
        self._write_lock = asyncio.Lock()

    async def bootstrap_default(self) -> ScopeDescriptor:
        existing = await self.default_scope()
        if existing is not None:
            return existing
        created = await self.create(
            ScopeDraft(
                title="Default",
                summary="Default context",
                idempotency_key=_DEFAULT_IDEMPOTENCY_KEY,
            )
        )
        await self.set_default(created.scope_id)
        return created

    async def create(self, draft: ScopeDraft, /) -> ScopeDescriptor:
        async with self._write_lock:
            return await self._create(draft)

    async def _create(self, draft: ScopeDraft) -> ScopeDescriptor:
        digest = _draft_digest(draft)
        try:
            async with self._database.transaction() as connection:
                existing = await self._repository.creation(connection, draft.idempotency_key)
                if existing is not None:
                    return await self._resolve_creation(connection, draft.idempotency_key, digest, existing)
                await self._validate_relationships(
                    connection,
                    scope_id=None,
                    parent_scope_id=draft.parent_scope_id,
                    context_references=draft.context_references,
                )
                return await self._repository.add(connection, self._id_factory(), draft, digest)
        except IntegrityError:
            # The transaction must roll back before reading the request that won
            # the unique-key race. An unrelated integrity failure remains visible.
            async with self._database.transaction() as connection:
                existing = await self._repository.creation(connection, draft.idempotency_key)
                if existing is None:
                    raise
                return await self._resolve_creation(connection, draft.idempotency_key, digest, existing)

    async def _resolve_creation(
        self,
        connection: AsyncConnection,
        idempotency_key: str,
        request_digest: str,
        creation: tuple[str, str],
        /,
    ) -> ScopeDescriptor:
        existing_digest, scope_id = creation
        if existing_digest != request_digest:
            raise ScopeIdempotencyConflictError(idempotency_key)
        return await self._required(connection, scope_id)

    async def get(self, scope_id: str, /) -> ScopeDescriptor:
        async with self._database.transaction() as connection:
            return await self._required(connection, scope_id)

    async def list(self) -> tuple[ScopeDescriptor, ...]:
        async with self._database.transaction() as connection:
            return await self._repository.list(connection)

    async def update(self, scope_id: str, mutation: ScopeMutation, /) -> ScopeDescriptor:
        async with self._write_lock:
            return await self._update(scope_id, mutation)

    async def _update(self, scope_id: str, mutation: ScopeMutation) -> ScopeDescriptor:
        async with self._database.transaction() as connection:
            await self._repository.lock_hierarchy(connection)
            current = await self._required(connection, scope_id)
            if current.version != mutation.expected_version:
                raise ScopeVersionConflictError(scope_id, mutation.expected_version, current.version)
            await self._validate_relationships(
                connection,
                scope_id=scope_id,
                parent_scope_id=mutation.parent_scope_id,
                context_references=mutation.context_references,
            )
            if not await self._repository.replace(connection, scope_id, mutation):
                refreshed = await self._required(connection, scope_id)
                raise ScopeVersionConflictError(scope_id, mutation.expected_version, refreshed.version)
            return await self._required(connection, scope_id)

    async def default_scope(self) -> ScopeDescriptor | None:
        async with self._database.transaction() as connection:
            scope_id = await self._repository.default_scope_id(connection)
            return None if scope_id is None else await self._required(connection, scope_id)

    async def set_default(self, scope_id: str, /) -> ScopeDescriptor:
        async with self._write_lock:
            try:
                return await self._set_default(scope_id)
            except IntegrityError:
                async with self._database.transaction() as connection:
                    if await self._repository.default_scope_id(connection) is None:
                        raise
                    scope = await self._required(connection, scope_id)
                    await self._repository.set_default(connection, scope_id)
                    return scope

    async def _set_default(self, scope_id: str) -> ScopeDescriptor:
        async with self._database.transaction() as connection:
            scope = await self._required(connection, scope_id)
            await self._repository.set_default(connection, scope_id)
            return scope

    async def bind(self, key: ScopeBindingKey, scope_id: str, /) -> ScopeBinding:
        async with self._write_lock:
            try:
                return await self._bind(key, scope_id)
            except IntegrityError:
                async with self._database.transaction() as connection:
                    if await self._repository.binding(connection, key) is None:
                        raise
                    await self._required(connection, scope_id)
                    return await self._repository.set_binding(connection, key, scope_id)

    async def _bind(self, key: ScopeBindingKey, scope_id: str) -> ScopeBinding:
        async with self._database.transaction() as connection:
            await self._required(connection, scope_id)
            return await self._repository.set_binding(connection, key, scope_id)

    async def binding(self, key: ScopeBindingKey, /) -> ScopeBinding | None:
        async with self._database.transaction() as connection:
            return await self._repository.binding(connection, key)

    async def clear_binding(self, key: ScopeBindingKey, /) -> bool:
        async with self._write_lock, self._database.transaction() as connection:
            return await self._repository.clear_binding(connection, key)

    async def resolve_binding(
        self,
        *,
        explicit_scope_id: str | None = None,
        binding_keys: Sequence[ScopeBindingKey] = (),
    ) -> ScopeDescriptor:
        async with self._database.transaction() as connection:
            if explicit_scope_id is not None:
                return await self._required(connection, explicit_scope_id)
            for key in binding_keys:
                binding = await self._repository.binding(connection, key)
                if binding is not None:
                    return await self._required(connection, binding.scope_id)
            default_scope_id = await self._repository.default_scope_id(connection)
            if default_scope_id is None:
                raise ScopeBindingNotFoundError
            return await self._required(connection, default_scope_id)

    async def resolve_selection(self, selection: ScopeSelection, /) -> tuple[ScopeDescriptor, ...]:
        async with self._database.transaction() as connection:
            if selection.mode == "all":
                return await self._repository.list(connection)
            if selection.mode == "exact":
                resolved = await self._repository.get_many(connection, selection.scope_ids)
                found = {scope.scope_id for scope in resolved}
                if missing := next((scope_id for scope_id in selection.scope_ids if scope_id not in found), None):
                    raise ScopeNotFoundError(missing)
                return resolved
            root_scope_id = selection.root_scope_id
            if root_scope_id is None:
                raise AssertionError
            scopes = await self._repository.list(connection)
            return _subtree(scopes, root_scope_id)

    async def _required(self, connection: AsyncConnection, scope_id: str) -> ScopeDescriptor:
        scope = await self._repository.get(connection, scope_id)
        if scope is None:
            raise ScopeNotFoundError(scope_id)
        return scope

    async def _validate_relationships(
        self,
        connection: AsyncConnection,
        *,
        scope_id: str | None,
        parent_scope_id: str | None,
        context_references: tuple[str, ...],
    ) -> None:
        if scope_id is not None and parent_scope_id == scope_id:
            raise ScopeRelationshipError("Parent", "a Scope cannot parent itself")
        if parent_scope_id is not None:
            parent = await self._required(connection, parent_scope_id)
            while parent.parent_scope_id is not None:
                if parent.parent_scope_id == scope_id:
                    raise ScopeRelationshipError("Parent", "relationships must be acyclic")
                parent = await self._required(connection, parent.parent_scope_id)
        for referenced_scope_id in context_references:
            if referenced_scope_id == scope_id:
                raise ScopeRelationshipError(  # noqa: TRY003
                    "Context Reference",
                    "a Scope cannot reference itself",
                )
            await self._required(connection, referenced_scope_id)


def generate_scope_id() -> str:
    """Generate the RFC-defined 128-bit opaque Scope identity."""

    value = int.from_bytes(secrets.token_bytes(16), "big")
    encoded = "".join(_CROCKFORD[(value >> shift) & 31] for shift in range(125, -1, -5))
    return f"scp_{encoded}"


def _draft_digest(draft: ScopeDraft) -> str:
    payload = draft.model_dump_json(exclude={"idempotency_key"})
    return hashlib.sha256(payload.encode()).hexdigest()


def _subtree(scopes: tuple[ScopeDescriptor, ...], root_scope_id: str) -> tuple[ScopeDescriptor, ...]:
    by_id = {scope.scope_id: scope for scope in scopes}
    root = by_id.get(root_scope_id)
    if root is None:
        raise ScopeNotFoundError(root_scope_id)
    children: dict[str, list[ScopeDescriptor]] = {}
    for scope in scopes:
        if scope.parent_scope_id is not None:
            children.setdefault(scope.parent_scope_id, []).append(scope)
    resolved = [root]
    pending = deque([root.scope_id])
    visited = {root.scope_id}
    while pending:
        parent_scope_id = pending.popleft()
        for child in children.get(parent_scope_id, ()):
            if child.scope_id in visited:
                continue
            visited.add(child.scope_id)
            resolved.append(child)
            pending.append(child.scope_id)
    return tuple(resolved)
