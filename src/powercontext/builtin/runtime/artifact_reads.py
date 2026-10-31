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

"""Invocation-scoped Artifact reads and shared custom-family FTS adapters."""

from contextlib import AsyncExitStack, asynccontextmanager
from typing import get_args

from pydantic import JsonValue, RootModel
from sqlalchemy import select

from powercontext.artifact_plugins import ArtifactReferencePage, ArtifactSnapshot
from powercontext.artifacts import Artifact, ArtifactRef
from powercontext.artifacts.search import (
    ArtifactSearchExecutionContext,
)
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.cursor_codec import SignedCursorCodec
from powercontext.builtin.persistence.custom_artifacts import StoredArtifactContent, custom_artifact_repository
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.tables import ARTIFACT_FAMILY_SCHEMAS_TABLE, ARTIFACT_HEADS_TABLE
from powercontext.builtin.records import BaseArtifactFamily, BaseValueNotFoundError, InvalidBaseAccessRequestError
from powercontext.builtin.runtime.artifact_search import ArtifactSearchService
from powercontext.builtin.runtime.custom_artifact_search import CustomArtifactSearcher
from powercontext.server.authz import AccessAction, BuiltinAuthorizationProvider, ResourceRef


class _StoredJsonContent(RootModel[dict[str, JsonValue]]):
    pass


class ArtifactReadSession:
    """Constrain reads to one Scope and record exact bodies eligible for generation lineage."""

    def __init__(
        self,
        database,
        scope_id,
        families,
        *,
        access=None,
        principal=None,
        audit=None,
        guard=None,
        embedding_model=None,
        search_service=None,
    ):
        self.database, self.scope_id = database, scope_id
        self.families = frozenset(families)
        self.access, self.principal, self.audit, self.guard = access, principal, audit, guard
        # Only the exact built-in Provider guarantees Scope-to-Artifact read inheritance.
        self._scope_covers_artifacts = access is None or type(access.provider) is BuiltinAuthorizationProvider
        self.refs: set[tuple[str, str, int]] = set()
        self.cursor = SignedCursorCodec()
        self.search_service = search_service or ArtifactSearchService(
            known_families=get_args(BaseArtifactFamily),
            fallback=lambda family: CustomArtifactSearcher(database, family, embedding_model),
        )

    def _family(self, family):
        if family not in self.families:
            raise InvalidBaseAccessRequestError("family", "is outside the invocation's declared read families")

    @asynccontextmanager
    async def _connection(self, family):
        self._family(family)
        async with AsyncExitStack() as resources:
            if self.access is not None:
                await resources.enter_async_context(self.access.defer_decision_audit())
            connection = await resources.enter_async_context(self.database.transaction())
            await connection.exec_driver_sql("SAVEPOINT pc_artifact_read")
            if self.guard is not None:
                await self.guard(connection)
            # The Worker guard already checks current Scope contribution in this transaction.
            if self.access is not None and (self.guard is None or not self._scope_covers_artifacts):
                await self.access.for_read(connection).require(
                    self.principal,
                    AccessAction.SCOPE_READ,
                    ResourceRef.scope(self.scope_id),
                    context=self.audit,
                )
            yield connection

    async def _repository(self, connection, family):
        known = await connection.scalar(
            select(ARTIFACT_FAMILY_SCHEMAS_TABLE.c.family)
            .where(
                ARTIFACT_FAMILY_SCHEMAS_TABLE.c.family == family,
            )
            .limit(1)
        )
        if known is not None:
            return custom_artifact_repository(family)
        if family in get_args(BaseArtifactFamily):
            artifact_type = type("StoredArtifact", (Artifact[_StoredJsonContent],), {"family": family})
            return ArtifactRepository((artifact_type,))
        raise BaseValueNotFoundError("artifact_family", family)

    async def read(self, family, artifact_id, revision):
        async with self._connection(family) as connection:
            repository = await self._repository(connection, family)
            try:
                artifact = (
                    await repository.latest(connection, self.scope_id, family, artifact_id)
                    if revision is None
                    else await repository.get(
                        connection,
                        self.scope_id,
                        ArtifactRef(family=family, artifact_id=artifact_id, revision=revision),
                    )
                )
            except RepositoryNotFoundError:
                if revision is None:
                    return None
                raise BaseValueNotFoundError("artifact_revision", (family, artifact_id, revision)) from None
            if self.access is not None and not self._scope_covers_artifacts and family != "topic-memory":
                await self.access.for_read(connection).require(
                    self.principal,
                    AccessAction.ARTIFACT_READ,
                    ResourceRef.artifact(self.scope_id, family=family, artifact_id=artifact_id),
                    context=self.audit,
                )
            ref = artifact.as_ref()
            self.refs.add((ref.family, ref.artifact_id, ref.revision))
            custom = isinstance(artifact.content, StoredArtifactContent)
            return ArtifactSnapshot(
                ref=ref,
                lineage=artifact.lineage,
                schema_version=artifact.content.schema_version if custom else None,
                content=artifact.content.content if custom else artifact.content.model_dump(mode="json", by_alias=True),
                source_refs=artifact.lineage.sources,
                artifact_refs=artifact.lineage.artifacts,
            )

    async def list(self, family, *, limit=50, cursor=None):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidBaseAccessRequestError("limit", "must be between 1 and 100")
        expected = {"scope": self.scope_id, "family": family, "limit": limit}
        after = self.cursor.after_text(cursor, expected)
        selected = []
        async with self._connection(family) as connection:
            await self._repository(connection, family)
            bound = None if self.access is None else self.access.for_read(connection)
            while len(selected) <= limit:
                rows = (
                    await connection.execute(
                        select(ARTIFACT_HEADS_TABLE.c.artifact_id, ARTIFACT_HEADS_TABLE.c.revision)
                        .where(
                            ARTIFACT_HEADS_TABLE.c.scope_id == self.scope_id,
                            ARTIFACT_HEADS_TABLE.c.family == family,
                            ARTIFACT_HEADS_TABLE.c.lifecycle_state == "active",
                            ARTIFACT_HEADS_TABLE.c.artifact_id > after,
                        )
                        .order_by(ARTIFACT_HEADS_TABLE.c.artifact_id)
                        .limit(100)
                    )
                ).all()
                if not rows:
                    break
                for artifact_id, revision in rows:
                    after = artifact_id
                    if bound is not None and not self._scope_covers_artifacts and family != "topic-memory":
                        decision = await bound.check(
                            self.principal,
                            AccessAction.ARTIFACT_READ,
                            ResourceRef.artifact(self.scope_id, family=family, artifact_id=artifact_id),
                            context=self.audit,
                        )
                        if not decision.allowed:
                            continue
                    selected.append(ArtifactRef(family=family, artifact_id=artifact_id, revision=revision))
                    if len(selected) > limit:
                        break
            items = tuple(selected[:limit])
            return ArtifactReferencePage(
                items=items,
                next_cursor=self.cursor.encode(expected, items[-1].artifact_id) if len(selected) > limit else None,
            )

    async def search(self, family, query=None, **parameters):
        self._family(family)
        async with self._connection(family):
            pass
        payload = dict(parameters)
        if query is not None:
            payload["query"] = query
        outcome = await self.search_service.search(
            self.scope_id,
            family,
            payload,
            execution_context=ArtifactSearchExecutionContext(
                access=self.access, principal=self.principal, audit=self.audit, trusted_local=self.access is None
            ),
        )
        return outcome
