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


"""Atomic subject-addressed Source writes over ordinary scopes and bindings."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from uuid import uuid4

from pydantic import JsonValue
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.database import AsyncDatabase, is_transaction_contention
from powercontext.builtin.persistence.profile import ProfilePolicyRepository
from powercontext.builtin.persistence.records import _canonical_source_text, _source_record
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.records import BaseValueConflictError, InvalidBaseAccessRequestError, SourceRecord
from powercontext.builtin.scope.application import generate_scope_id
from powercontext.builtin.scope.errors import ScopeNotFoundError
from powercontext.builtin.scope.models import ScopeBinding, ScopeBindingKey, ScopeDraft
from powercontext.builtin.scope.repository import ScopeRepository
from powercontext.builtin.sources import ContentSource
from powercontext.sources import SourceMaterialization

# The HTTP boundary supplies authorization/bootstrap callbacks. Embedded runtimes
# retain their existing trusted-local behavior.
SubjectAuthorization = Callable[[AsyncConnection, str, bool, bool], Awaitable[None]]


class SubjectSourceService:
    def __init__(self, database: AsyncDatabase, sources: SourceRepository):
        self._database = database
        self._sources = sources
        self._scopes = ScopeRepository()
        self._policies = ProfilePolicyRepository()
        self._lock = asyncio.Lock()

    async def create(
        self,
        scope_id: str,
        subject_key: str,
        content: JsonValue,
        *,
        subject_type: str = "user",
        subject_scope_id: str | None = None,
        authorize: SubjectAuthorization | None = None,
    ) -> tuple[str, tuple[SourceRecord, SourceRecord]]:
        if subject_type != "user":
            raise InvalidBaseAccessRequestError("subject_type", "only user is supported")
        key = ScopeBindingKey(integration="subject", kind=subject_type, external_id=subject_key)
        source = ContentSource(
            name=f"source_{uuid4().hex}",
            materialization=SourceMaterialization.CAPTURED,
            content=_canonical_source_text(content),
            wire_content=content,
            wire_content_present=True,
        )
        resolved_scope: list[str] = []
        async with self._lock:
            for attempt in range(3):
                try:
                    return await self._write(scope_id, key, subject_scope_id, source, authorize, resolved_scope)
                except OperationalError as error:
                    if not is_transaction_contention(error) or attempt == 2:
                        raise
                    await asyncio.sleep(0.02 * (attempt + 1))
                except IntegrityError:
                    # Roll back the entire losing transaction, including its new Scope.
                    async with self._database.transaction() as connection:
                        winner = await self._scopes.binding(connection, key)
                    if winner is None or attempt == 2:
                        raise
        raise AssertionError("unreachable")

    async def _write(
        self,
        scope_id: str,
        key: ScopeBindingKey,
        requested_scope: str | None,
        source: ContentSource,
        authorize: SubjectAuthorization | None,
        resolved_scope: list[str],
    ) -> tuple[str, tuple[SourceRecord, SourceRecord]]:
        async with self._database.transaction() as connection:
            if await self._scopes.get(connection, scope_id) is None:
                raise ScopeNotFoundError(scope_id)
            binding = await self._scopes.binding(connection, key)
            _pin_binding(binding, resolved_scope, scope_id)
            new_binding = binding is None
            new_scope = new_binding and requested_scope is None
            if binding is not None:
                target = binding.scope_id
                if requested_scope is not None and target != requested_scope:
                    raise BaseValueConflictError("subject_scope", (scope_id,))
            else:
                target = generate_scope_id() if requested_scope is None else requested_scope
            if target == scope_id:
                raise InvalidBaseAccessRequestError("scope_id", "distinct_scopes_required")
            if not new_scope and await self._scopes.get(connection, target) is None:
                raise ScopeNotFoundError(target)
            # Checks and the new Scope's access relationship share this transaction.
            if authorize is not None:
                await authorize(connection, target, new_binding, new_scope)
            if new_scope:
                draft = ScopeDraft(
                    title="Subject", summary="Subject evidence", idempotency_key=f"subject-{uuid4().hex}"
                )
                await self._scopes.add(connection, target, draft, f"subject-{source.name}")
                await self._policies.create(connection, target, enabled=True)
            if new_binding:
                await self._scopes.ensure_binding(connection, key, target)
            stored = {}
            for actual_scope in sorted((scope_id, target)):
                stored[actual_scope] = await self._sources.add(connection, actual_scope, source)
            return target, (_source_record(scope_id, stored[scope_id]), _source_record(target, stored[target]))


def _pin_binding(binding: ScopeBinding | None, resolved_scope: list[str], origin_scope_id: str) -> None:
    if resolved_scope and (binding is None or binding.scope_id != resolved_scope[0]):
        raise BaseValueConflictError("subject_scope", (origin_scope_id,))
    if binding is not None and not resolved_scope:
        # A retry must not silently reroute an already resolved subject.
        resolved_scope.append(binding.scope_id)
