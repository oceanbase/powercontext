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

"""Explicit scoped merge and restoration for registered Artifact Family services."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import nullcontext
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactLineage, ArtifactRef
from powercontext.artifacts.search import ArtifactSearchExecutionContext
from powercontext.builtin.artifacts.merge import ArtifactMergeService
from powercontext.builtin.artifacts.merge_models import (
    ArtifactMergeMutationResult,
    ArtifactMergeRead,
    ArtifactMergeRecord,
    ArtifactMergeRestorationPreview,
    ArtifactMergeRestoreOperation,
)
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.tables import ARTIFACT_TAGS_TABLE
from powercontext.builtin.records import BaseOperationNotSupportedError
from powercontext.builtin.sources import validate_scope_id
from powercontext.builtin.sources.content import ArtifactRestorationOutcome
from powercontext.builtin.tags import normalize_tags


class ArtifactMergeAccess:
    """Apply the shared access service; a missing context means access control is disabled."""

    def __init__(self, family: str) -> None:
        self.family = family

    @staticmethod
    def subject(context: ArtifactSearchExecutionContext | None) -> str:
        principal = None if context is None else context.principal
        return "" if principal is None else f"{principal.type}:{principal.id}"

    async def authorize(
        self,
        connection: AsyncConnection,
        scope_id: str,
        context: ArtifactSearchExecutionContext | None,
        action: str,
        ref: ArtifactRef | None = None,
    ) -> None:
        from powercontext.server.authz import AccessAction, AccessDeniedError, ResourceRef

        if context is None:
            return
        if context.access is None:
            if not context.trusted_local:
                raise AccessDeniedError
            return
        # Prompt and Topic Memory are Scope-owned and have no Artifact owner.
        if action == "read" and (ref is None or ref.family in {"prompt", "topic-memory"}):
            await context.access.require_scope_read(
                context.principal, scope_id, connection=connection, context=context.audit
            )
            return
        access = context.access.with_connection(connection)
        if ref is None or action == "create":
            permission = AccessAction.SCOPE_READ if action == "read" else AccessAction.SCOPE_CONTRIBUTE
            resource = ResourceRef.scope(scope_id)
            await access.bootstrap_static_scope(context.principal, scope_id, context=context.audit)
        else:
            permission = AccessAction.ARTIFACT_READ if action == "read" else AccessAction.ARTIFACT_WRITE
            resource = ResourceRef.artifact(scope_id, family=ref.family, artifact_id=ref.artifact_id)
        await access.require(context.principal, permission, resource, context=context.audit)

    async def authorize_sources(self, connection: AsyncConnection, scope_id: str, context, sources) -> None:
        if sources:
            await self.authorize(connection, scope_id, context, "read")

    async def establish_owner(self, connection: AsyncConnection, scope_id: str, artifact_id: str, context) -> None:
        if context is None or context.access is None:
            return
        from powercontext.server.authz import ResourceRef

        await context.access.with_connection(connection).establish_artifact_owner(
            ResourceRef.artifact(scope_id, family=self.family, artifact_id=artifact_id),
            context.principal,
            idempotency_key=f"{self.family}-owner:{scope_id}:{artifact_id}",
            context=context.audit,
        )


class ArtifactMergeApplication:
    """Register Family services without choosing inputs or generating merge content.

    Each service supplies its own content, projection and permission adapter.
    Authenticated hosts pass their execution context explicitly; the default
    context belongs to the composed local application.
    When access control is enabled, the shared policy requires write authority
    over existing inputs. Creation establishes the new result's owner.
    """

    def __init__(
        self,
        database: AsyncDatabase,
        services: Sequence[ArtifactMergeService],
        *,
        default_context: Any,
        id_factory: Callable[[str], str] | None = None,
        restore_retry_budget: int = 3,
    ) -> None:
        self.database = database
        self.default_context = default_context
        self.id_factory = id_factory or (lambda _family: f"art_{uuid4().hex}")
        self.restore_retry_budget = restore_retry_budget
        self._services: dict[str, ArtifactMergeService] = {}
        for service in services:
            self.register(service)

    def register(self, service: ArtifactMergeService) -> None:
        """Register one explicitly configured service per Family."""

        if service.family in self._services:
            raise ValueError(f"Artifact merge Family is already registered: {service.family}")  # noqa: TRY003
        self._services[service.family] = service

    def for_scope(self, scope_id: str, family: str, /) -> ScopedArtifactMerge:
        scope_id = validate_scope_id(scope_id)
        try:
            service = self._services[family]
        except KeyError:
            raise BaseOperationNotSupportedError(
                "artifact_family", family, "registered Artifact merge adapter"
            ) from None
        return ScopedArtifactMerge(self, service, scope_id)


class ScopedArtifactMerge:
    """Explicit merge, signed restoration preview and whole-group restoration."""

    def __init__(self, application: ArtifactMergeApplication, service: ArtifactMergeService, scope_id: str) -> None:
        self.application = application
        self.service = service
        self.scope_id = scope_id
        self.family = service.family

    def _context(self, context: Any) -> Any:
        return self.application.default_context if context is None else context

    async def get(self, artifact_id: str, *, revision: int | None = None, context: Any = None) -> ArtifactMergeRecord:
        context = self._context(context)
        access = getattr(context, "access", None)
        async with (
            access.defer_decision_audit() if access is not None else nullcontext(),
            self.application.database.transaction(consistent_snapshot=True) as connection,
        ):
            return await self.service.get(connection, self.scope_id, artifact_id, context, revision=revision)

    async def merge(
        self,
        inputs: Sequence[ArtifactMergeRead],
        content: BaseModel,
        *,
        artifact_id: str | None = None,
        lineage: ArtifactLineage | None = None,
        context: Any = None,
    ) -> ArtifactMergeMutationResult:
        """Create a new identity from explicitly selected exact current reads."""

        context = self._context(context)
        async with self.application.database.transaction() as connection:
            plan = await self.service.inspect_merge(
                connection,
                self.scope_id,
                self.application.id_factory(self.family) if artifact_id is None else artifact_id,
                inputs,
                content,
                context,
                lineage=lineage,
            )
        prepared = await self.service.prepare_merge(plan)
        async with self.application.database.transaction() as connection:
            return await self.service.commit(connection, prepared, context)

    async def restoration_outcome(
        self, artifact_id: str, *, revision: int, context: Any = None
    ) -> ArtifactRestorationOutcome | None:
        """Read the saved exact group outcome from a restoration's primary revision."""

        context = self._context(context)
        access = getattr(context, "access", None)
        async with (
            access.defer_decision_audit() if access is not None else nullcontext(),
            self.application.database.transaction(consistent_snapshot=True) as connection,
        ):
            return await self.service.restoration_outcome(connection, self.scope_id, artifact_id, revision, context)

    async def preview_restoration(
        self,
        artifact_id: str,
        *,
        operation: ArtifactMergeRestoreOperation = "restore",
        revision: int | None = None,
        context: Any = None,
    ) -> ArtifactMergeRestorationPreview:
        context = self._context(context)
        access = getattr(context, "access", None)
        async with (
            access.defer_decision_audit() if access is not None else nullcontext(),
            self.application.database.transaction(consistent_snapshot=True) as connection,
        ):
            plan = await self.service.inspect_restore(
                connection, self.scope_id, artifact_id, context, operation=operation, revision=revision
            )
        return self.service.restoration_preview(plan)

    async def restore(
        self,
        artifact_id: str,
        *,
        operation: ArtifactMergeRestoreOperation = "restore",
        revision: int | None = None,
        preview_token: str | None = None,
        context: Any = None,
    ) -> ArtifactMergeMutationResult:
        context = self._context(context)
        for attempt in range(self.application.restore_retry_budget + 1):
            try:
                async with self.application.database.transaction() as connection:
                    plan = await self.service.inspect_restore(
                        connection,
                        self.scope_id,
                        artifact_id,
                        context,
                        operation=operation,
                        revision=revision,
                        preview_token=preview_token,
                    )
                prepared = await self.service.prepare_restore(plan)
                async with self.application.database.transaction() as connection:
                    return await self.service.commit(connection, prepared, context)
            except self.service.errors.conflict as error:
                if preview_token is not None:
                    if isinstance(error, self.service.errors.preview_stale):
                        raise
                    raise self.service.errors.preview_stale("restoration preview is stale") from error  # noqa: TRY003
                if attempt == self.application.restore_retry_budget:
                    raise
        raise AssertionError("restoration retry loop ended without a result")  # noqa: TRY003


async def merge_artifact_tags(
    connection: AsyncConnection,
    scope_id: str,
    result_id: str,
    input_ids: tuple[str, ...],
    *,
    family: str,
) -> None:
    """Copy the normalized union of tags within one Scope and Family."""

    rows = (
        await connection.execute(
            select(ARTIFACT_TAGS_TABLE.c.tag_key, ARTIFACT_TAGS_TABLE.c.tag)
            .where(
                ARTIFACT_TAGS_TABLE.c.scope_id == scope_id,
                ARTIFACT_TAGS_TABLE.c.family == family,
                ARTIFACT_TAGS_TABLE.c.artifact_id.in_(input_ids),
                ARTIFACT_TAGS_TABLE.c.target_type == "artifact",
            )
            .order_by(ARTIFACT_TAGS_TABLE.c.tag_key, ARTIFACT_TAGS_TABLE.c.artifact_id)
            .with_for_update()
        )
    ).all()
    labels: dict[str, str] = {}
    for row in rows:
        labels.setdefault(str(row.tag_key), str(row.tag))
    for key, label in normalize_tags(tuple(labels.values())).items():
        await connection.execute(
            insert(ARTIFACT_TAGS_TABLE).values(
                scope_id=scope_id,
                family=family,
                artifact_id=result_id,
                target_type="artifact",
                target_id=result_id,
                tag_key=key,
                tag_key_hash=sha256(key.encode()).digest(),
                tag=label,
                assigned_at=datetime.now(UTC),
            )
        )
