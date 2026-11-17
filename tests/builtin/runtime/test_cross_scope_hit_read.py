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

"""Opening a cross-Scope hit requires the body permission, not only permission to use the Scope.

A configured PDP can allow `scope.read` while refusing `artifact.read`. Every built-in role that grants
`scope.read` also grants `artifact.read` as a child action, which is exactly why a Scope-only check looked
correct and why this case was previously unproven.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from powercontext.artifacts import ArtifactAddress, ArtifactRef, ArtifactSearchExecutionContext
from powercontext.builtin.authz import (
    AccessAction,
    AccessAuditContext,
    AccessDecision,
    AccessDeniedError,
    AccessRole,
    CreateBinding,
    PrincipalRef,
    ResourceRef,
)
from powercontext.builtin.authz.composition import open_builtin_access_control
from powercontext.builtin.authz.service import AccessControlService
from powercontext.builtin.persistence.records import ArtifactWrite
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft

ADMIN = PrincipalRef(type="service", id="cross-scope-hit-admin")
READER = PrincipalRef(type="user", id="cross-scope-hit-reader")
DEPLOYMENT = "cross-scope-open-hit"

_EXPERIENCE = {"situation": "SECRET-SITUATION", "action": "a", "outcome": "o", "lesson": "l"}


class _SeparatedPolicy:
    """Delegate to the real policy while refusing one action, so the two checks stay distinguishable."""

    def __init__(self, inner: Any, denied: AccessAction) -> None:
        self._inner = inner
        self._denied = denied

    async def check(self, request, /):
        if request.action is self._denied:
            return AccessDecision(False, "separated-body-permission", None)
        return await self._inner.check(request)

    async def check_batch(self, requests: Sequence[Any], /):
        return tuple([await self.check(request) for request in requests])

    async def resolve_resource_filter(self, request, /):
        return await self._inner.resolve_resource_filter(request)


def _context(access: AccessControlService) -> ArtifactSearchExecutionContext:
    return ArtifactSearchExecutionContext(
        principal=READER,
        access=access,
        audit=AccessAuditContext(transport="sdk", operation="search_cross_scope_artifacts"),
        trusted_local=False,
    )


def test_open_hit_denies_the_body_before_returning_it_and_allows_the_exact_revision(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'hit-read.db'}")
        # Access owns its own tables, so it must exist before the Runtime opens the shared file.
        async with (
            open_builtin_access_control(
                database, bootstrap_administrators=(ADMIN,), deployment_id=DEPLOYMENT
            ) as access,
            open_builtin_runtime(BuiltinConfig(database=database)) as runtime,
        ):
            assert runtime.scopes is not None
            home = (
                await runtime.scopes.create(ScopeDraft(title="Home", summary="H", idempotency_key="hit-home"))
            ).scope_id
            library = (
                await runtime.scopes.create(
                    ScopeDraft(title="Library", summary="L", parent_scope_id=home, idempotency_key="hit-lib")
                )
            ).scope_id
            content: dict[str, Any] = dict(_EXPERIENCE)
            created = await runtime.records.for_scope(library).create_artifact(
                "experience", ArtifactWrite(content=content)
            )
            committed = ArtifactRef(family=created.family, artifact_id=created.artifact_id, revision=created.revision)
            target = ArtifactAddress(scope_id=library, artifact=committed)

            # The reader may use the Scope that holds the Artifact.
            await access.create_binding(
                ADMIN,
                CreateBinding(
                    subject=READER,
                    resource=ResourceRef.scope(library),
                    role=AccessRole.SCOPE_VIEWER,
                    idempotency_key="hit-reader-library",
                ),
                context=AccessAuditContext(transport="sdk", operation="bind"),
            )
            await access.require(
                READER,
                AccessAction.SCOPE_READ,
                ResourceRef.scope(library),
                context=AccessAuditContext(transport="sdk", operation="scope-read"),
            )

            # A policy that separates the two actions must deny the body through the hit.
            original_provider = access.provider
            access.provider = _SeparatedPolicy(original_provider, AccessAction.ARTIFACT_READ)
            with pytest.raises(AccessDeniedError):
                await runtime.cross_scope.for_scope(home).open_hit(target, execution_context=_context(access))

            # The same exact revision is readable once the body permission holds again.
            access.provider = original_provider
            record = await runtime.cross_scope.for_scope(home).open_hit(target, execution_context=_context(access))
            assert (record.family, record.artifact_id, record.revision) == (
                committed.family,
                committed.artifact_id,
                committed.revision,
            )
            assert record.content["situation"] == "SECRET-SITUATION"

    asyncio.run(scenario())
