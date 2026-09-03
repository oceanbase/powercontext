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

"""Exact Artifact delivery across Scope ownership boundaries."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactAddress, ArtifactRef
from powercontext.builtin.artifacts.experience import Experience
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.codec import dump_model
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.experience_index import ExperienceIndex
from powercontext.builtin.persistence.tables import ARTIFACT_PUBLICATIONS_TABLE
from powercontext.builtin.scope import ScopeApplication
from powercontext.errors import PowerContextError
from powercontext.limits import MAX_SCOPE_IDEMPOTENCY_KEY_LENGTH

PublicationIdFactory = Callable[[], str]
_CROCKFORD = "0123456789abcdefghjkmnpqrstvwxyz"


class ArtifactPublicationRequest(BaseModel):
    """Select one exact source revision for delivery into a target Scope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: ArtifactAddress
    target_scope_id: str
    idempotency_key: str

    @field_validator("target_scope_id", "idempotency_key")
    @classmethod
    def validate_text(cls, value: str, info) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError(f"{info.field_name} must be non-empty and trimmed")  # noqa: TRY003
        if info.field_name == "idempotency_key" and len(value) > MAX_SCOPE_IDEMPOTENCY_KEY_LENGTH:
            raise ValueError(  # noqa: TRY003
                f"idempotency_key must not exceed {MAX_SCOPE_IDEMPOTENCY_KEY_LENGTH} characters"
            )
        return value

    @model_validator(mode="after")
    def require_scope_boundary(self) -> ArtifactPublicationRequest:
        if self.source.scope_id == self.target_scope_id:
            raise ValueError("publication requires different source and target Scopes")  # noqa: TRY003
        return self


class ArtifactPublication(BaseModel):
    """The immutable source and target addresses created by one publication."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: ArtifactAddress
    target: ArtifactAddress
    content_digest: str


class ArtifactPublicationConflictError(PowerContextError, RuntimeError):
    """Report reuse of an idempotency key for a different publication."""


class ArtifactPublicationUnsupportedError(PowerContextError, RuntimeError):
    """Report an Artifact family that cannot yet form a complete target revision."""

    def __init__(self, family: str) -> None:
        self.family = family
        super().__init__(f"Artifact publication is not supported for family: {family}")


class ArtifactPublicationApplication:
    def __init__(
        self,
        database: AsyncDatabase,
        artifacts: ArtifactRepository,
        scopes: ScopeApplication,
        *,
        experience_index: ExperienceIndex,
        id_factory: PublicationIdFactory | None = None,
    ) -> None:
        self._database = database
        self._artifacts = artifacts
        self._scopes = scopes
        self._experience_index = experience_index
        self._id_factory = generate_publication_artifact_id if id_factory is None else id_factory

    async def publish(self, request: ArtifactPublicationRequest, /) -> ArtifactPublication:
        await self._scopes.get(request.source.scope_id)
        await self._scopes.get(request.target_scope_id)
        try:
            async with self._database.transaction() as connection:
                return await self._publish(connection, request)
        except IntegrityError:
            async with self._database.transaction() as connection:
                existing = await self._find_request(connection, request.target_scope_id, request.idempotency_key)
            if existing is None:
                raise
            return _resolve_request(existing, request)

    async def _publish(
        self,
        connection: AsyncConnection,
        request: ArtifactPublicationRequest,
    ) -> ArtifactPublication:
        source = await self._artifacts.get(connection, request.source.scope_id, request.source.artifact)
        if source.family == "memory":
            raise ArtifactPublicationUnsupportedError(source.family)
        existing = await self._find_request(connection, request.target_scope_id, request.idempotency_key)
        if existing is not None:
            return _resolve_request(existing, request)

        content_digest = hashlib.sha256(dump_model(source.content, kind="artifact", name=source.family)).hexdigest()
        target = await self._artifacts.copy_exact(
            connection,
            request.target_scope_id,
            self._id_factory(),
            request.source,
            source,
            content_digest,
        )
        if isinstance(target, Experience):
            await self._experience_index.replace(connection, request.target_scope_id, target)
        publication = ArtifactPublication(
            source=request.source,
            target=ArtifactAddress(scope_id=request.target_scope_id, artifact=target.as_ref()),
            content_digest=content_digest,
        )
        await connection.execute(
            insert(ARTIFACT_PUBLICATIONS_TABLE).values(
                **_publication_row(publication),
                idempotency_key=request.idempotency_key,
            )
        )
        return publication

    async def get(self, target: ArtifactAddress, /) -> ArtifactPublication | None:
        async with self._database.transaction() as connection:
            row = (
                (
                    await connection.execute(
                        select(ARTIFACT_PUBLICATIONS_TABLE).where(
                            ARTIFACT_PUBLICATIONS_TABLE.c.target_scope_id == target.scope_id,
                            ARTIFACT_PUBLICATIONS_TABLE.c.target_family == target.artifact.family,
                            ARTIFACT_PUBLICATIONS_TABLE.c.target_artifact_id == target.artifact.artifact_id,
                            ARTIFACT_PUBLICATIONS_TABLE.c.target_revision == target.artifact.revision,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            return None if row is None else _decode_publication(row)

    async def _find_request(
        self,
        connection: AsyncConnection,
        target_scope_id: str,
        idempotency_key: str,
    ) -> ArtifactPublication | None:
        row = (
            (
                await connection.execute(
                    select(ARTIFACT_PUBLICATIONS_TABLE).where(
                        ARTIFACT_PUBLICATIONS_TABLE.c.target_scope_id == target_scope_id,
                        ARTIFACT_PUBLICATIONS_TABLE.c.idempotency_key == idempotency_key,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _decode_publication(row)


def generate_publication_artifact_id() -> str:
    value = int.from_bytes(secrets.token_bytes(16), "big")
    encoded = "".join(_CROCKFORD[(value >> shift) & 31] for shift in range(125, -1, -5))
    return f"pub_{encoded}"


def _resolve_request(
    existing: ArtifactPublication,
    request: ArtifactPublicationRequest,
) -> ArtifactPublication:
    if existing.source != request.source:
        raise ArtifactPublicationConflictError(request.idempotency_key)
    return existing


def _publication_row(publication: ArtifactPublication) -> dict[str, object]:
    return {
        "target_scope_id": publication.target.scope_id,
        "target_family": publication.target.artifact.family,
        "target_artifact_id": publication.target.artifact.artifact_id,
        "target_revision": publication.target.artifact.revision,
        "source_scope_id": publication.source.scope_id,
        "source_family": publication.source.artifact.family,
        "source_artifact_id": publication.source.artifact.artifact_id,
        "source_revision": publication.source.artifact.revision,
        "content_digest": publication.content_digest,
    }


def _decode_publication(row: Mapping[Any, Any]) -> ArtifactPublication:
    return ArtifactPublication(
        source=ArtifactAddress(
            scope_id=str(row["source_scope_id"]),
            artifact=ArtifactRef(
                family=str(row["source_family"]),
                artifact_id=str(row["source_artifact_id"]),
                revision=int(row["source_revision"]),
            ),
        ),
        target=ArtifactAddress(
            scope_id=str(row["target_scope_id"]),
            artifact=ArtifactRef(
                family=str(row["target_family"]),
                artifact_id=str(row["target_artifact_id"]),
                revision=int(row["target_revision"]),
            ),
        ),
        content_digest=str(row["content_digest"]),
    )


__all__ = [
    "ArtifactPublication",
    "ArtifactPublicationApplication",
    "ArtifactPublicationConflictError",
    "ArtifactPublicationRequest",
    "ArtifactPublicationUnsupportedError",
]
