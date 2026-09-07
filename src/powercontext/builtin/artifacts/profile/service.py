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

"""Subject routing and manual lifecycle operations for Profile Artifacts."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from typing import Any, cast
from uuid import uuid4

import rfc8785
from pydantic import JsonValue
from sqlalchemy.exc import IntegrityError

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.profile.models import (
    LOCAL_PROFILE_ARTIFACT_ID,
    PROFILE_FAMILY,
    USER_PROFILE_ARTIFACT_ID,
    Profile,
    ProfileContent,
    ProfileDraft,
    ProfileGenerationMode,
    ProfilePolicy,
    ProfileRecord,
    ResolvedProfileTarget,
    SourceAddress,
    SubjectRoot,
    SubjectSourceProjection,
    SubjectSourceWrite,
)
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import RepositoryNotFoundError, StoredPayloadConflictError
from powercontext.builtin.persistence.profile import SubjectProfileRepository, utc_now
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.records import (
    ArtifactAlreadyExistsError,
    ArtifactRevisionPreconditionError,
    BaseValueConflictError,
    BaseValueNotFoundError,
    InvalidBaseAccessRequestError,
)
from powercontext.builtin.scope.application import generate_scope_id
from powercontext.builtin.scope.models import ScopeDraft
from powercontext.builtin.scope.repository import ScopeRepository
from powercontext.builtin.sources import ContentSource, ContentSourceInternal, ContentSourceTarget
from powercontext.sources import Source, SourceMaterialization

IdFactory = Callable[[str], str]


class SubjectRootNotFoundError(LookupError):
    def __init__(self, subject_key: str) -> None:
        self.subject_key = subject_key
        super().__init__("Subject Root was not found")


class SubjectRootInvariantError(ValueError):
    """Report an operation that would violate a registered Subject Root."""


class RelationalProfileService:
    """Implement the durable, inference-independent Profile vertical slice."""

    def __init__(
        self,
        database: AsyncDatabase,
        sources: SourceRepository,
        artifacts: ArtifactRepository,
        /,
        *,
        metadata: SubjectProfileRepository | None = None,
        scopes: ScopeRepository | None = None,
        id_factory: IdFactory | None = None,
    ) -> None:
        self._database = database
        self._sources = sources
        self._artifacts = artifacts
        self._metadata = SubjectProfileRepository() if metadata is None else metadata
        self._scopes = ScopeRepository() if scopes is None else scopes
        self._id_factory = _resource_id if id_factory is None else id_factory
        self._root_creation_lock = asyncio.Lock()

    async def resolve_subject(self, subject_key: str, /, *, create_if_absent: bool = True) -> tuple[SubjectRoot, bool]:
        key = _subject_key(subject_key)
        async with self._database.transaction() as connection:
            existing = await self._metadata.subject_root(connection, key)
        if existing is not None:
            return existing, False
        if not create_if_absent:
            raise SubjectRootNotFoundError(key)

        # The database constraints remain authoritative across processes. The
        # local lock avoids creating and rolling back redundant Root candidates
        # for concurrent requests handled by this Runtime instance.
        async with self._root_creation_lock:
            try:
                async with self._database.transaction() as connection:
                    existing = await self._metadata.subject_root(connection, key)
                    if existing is not None:
                        return existing, False
                    root_scope_id = generate_scope_id()
                    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
                    draft = ScopeDraft(
                        title="User profile root",
                        summary="Root Scope for one caller-managed user profile.",
                        idempotency_key=f"profile-root:{digest}",
                    )
                    await self._scopes.add(
                        connection,
                        root_scope_id,
                        draft,
                        hashlib.sha256(draft.model_dump_json().encode("utf-8")).hexdigest(),
                    )
                    created = await self._metadata.add_subject_root(connection, key, root_scope_id)
                    await self._metadata.add_default_policy(connection, root_scope_id)
                    return created, True
            except IntegrityError:
                # The unique Subject/Root constraints select the winner across
                # Runtime instances; resolve it only after this transaction rolls back.
                pass

            async with self._database.transaction() as connection:
                winner = await self._metadata.subject_root(connection, key)
            if winner is None:
                raise SubjectRootInvariantError(  # noqa: TRY003
                    "concurrent Subject Root creation did not produce a mapping"
                )
            return winner, False

    async def resolve_target(
        self,
        *,
        subject_key: str | None = None,
        scope_id: str | None = None,
    ) -> ResolvedProfileTarget:
        if (subject_key is None) == (scope_id is None):
            raise InvalidBaseAccessRequestError("target", "must provide exactly one of subject_key or scope_id")
        if subject_key is not None:
            root, _ = await self.resolve_subject(subject_key, create_if_absent=False)
            return _subject_target(root)
        if scope_id is None:
            raise AssertionError
        async with self._database.transaction() as connection:
            subject = await self._metadata.root_subject(connection, scope_id)
            if subject is not None:
                return _subject_target(subject)
            if await self._scopes.get(connection, scope_id) is None:
                raise BaseValueNotFoundError("artifact", (scope_id, PROFILE_FAMILY, LOCAL_PROFILE_ARTIFACT_ID))
        return ResolvedProfileTarget(scope_id=scope_id, artifact_id=LOCAL_PROFILE_ARTIFACT_ID)

    async def enable_local_profile(
        self,
        scope_id: str,
        /,
    ) -> ProfilePolicy:
        async with self._database.transaction() as connection:
            if await self._metadata.root_subject(connection, scope_id) is not None:
                raise SubjectRootInvariantError(  # noqa: TRY003
                    "Subject Roots already have an implicit user Profile policy"
                )
            if await self._scopes.get(connection, scope_id) is None:
                raise BaseValueNotFoundError("artifact", (scope_id, PROFILE_FAMILY, LOCAL_PROFILE_ARTIFACT_ID))
            existing = await self._metadata.policy(connection, scope_id)
            if existing is not None:
                return existing
            return await self._metadata.add_default_policy(connection, scope_id)

    async def route_source(
        self,
        origin_scope_id: str,
        subject_key: str,
        source: Source,
        /,
    ) -> SubjectSourceWrite:
        root, _ = await self.resolve_subject(subject_key)
        if source.name == "":
            raise InvalidBaseAccessRequestError("source_id", "must not be blank")
        try:
            async with self._database.transaction() as connection:
                if await self._scopes.get(connection, origin_scope_id) is None:
                    raise BaseValueNotFoundError("source", (origin_scope_id, source.name))
                if origin_scope_id == root.root_scope_id:
                    stored = await self._sources.add(connection, origin_scope_id, source)
                    await self._metadata.mark_pending(connection, root.root_scope_id, stored.journal_position)
                    return SubjectSourceWrite(
                        subject=root,
                        origin_ref=stored.ref,
                        root_ref=stored.ref,
                        origin_position=stored.journal_position,
                        root_position=stored.journal_position,
                        status="already_in_root",
                    )

                # Lock journal heads in deterministic Scope order so two
                # cross-Scope writes cannot deadlock by choosing opposite order.
                stored_by_scope = {}
                for target_scope_id in sorted((origin_scope_id, root.root_scope_id)):
                    stored_by_scope[target_scope_id] = await self._sources.add(connection, target_scope_id, source)
                origin = stored_by_scope[origin_scope_id]
                projected = stored_by_scope[root.root_scope_id]
                projection = SubjectSourceProjection(
                    subject_key=root.subject_key,
                    origin=SourceAddress(
                        scope_id=origin_scope_id,
                        source_type=origin.ref.source_type,
                        source_id=origin.ref.source_id,
                    ),
                    projected=SourceAddress(
                        scope_id=root.root_scope_id,
                        source_type=projected.ref.source_type,
                        source_id=projected.ref.source_id,
                    ),
                    content_digest=_source_content_digest(source),
                    created_at=utc_now(),
                )
                existing = await self._metadata.projection_for_origin(connection, projection.origin)
                if existing is None:
                    await self._metadata.add_projection(connection, projection)
                elif existing != projection.model_copy(update={"created_at": existing.created_at}):
                    raise BaseValueConflictError("source", (origin_scope_id, source.name))
                await self._metadata.mark_pending(connection, root.root_scope_id, projected.journal_position)
        except StoredPayloadConflictError as error:
            raise BaseValueConflictError("source", (origin_scope_id, source.name)) from error
        return SubjectSourceWrite(
            subject=root,
            origin_ref=origin.ref,
            root_ref=projected.ref,
            origin_position=origin.journal_position,
            root_position=projected.journal_position,
            status="committed",
        )

    async def create_profile(
        self,
        target: ResolvedProfileTarget,
        content: str,
        /,
        *,
        reason: str,
    ) -> ProfileRecord:
        _reason(reason)
        profile_content = ProfileContent(content=content)
        async with self._database.transaction() as connection:
            try:
                await self._artifacts.latest(connection, target.scope_id, PROFILE_FAMILY, target.artifact_id)
            except RepositoryNotFoundError:
                pass
            else:
                raise ArtifactAlreadyExistsError(PROFILE_FAMILY, target.artifact_id, use_replace=True)
            source = _profile_command_source(
                source_id=self._id_factory("source"),
                scope_id=target.scope_id,
                artifact_id=target.artifact_id,
                revision=1,
                operation="artifact_create",
                content=profile_content,
            )
            stored = await self._sources.add(connection, target.scope_id, source)
            profile = cast(
                Profile,
                await self._artifacts.create(
                    connection,
                    target.scope_id,
                    target.artifact_id,
                    ProfileDraft(content=profile_content, sources=(stored.ref,)),
                ),
            )
            generation = await self._metadata.add_revision_metadata(
                connection,
                scope_id=target.scope_id,
                artifact_ref=profile.as_ref(),
                generation_mode="manual_create",
                operation_reason=reason,
            )
        return ProfileRecord(target=target, profile=profile, generation=generation, etag=_etag(profile.revision))

    async def get_profile(self, target: ResolvedProfileTarget, /, *, revision: int | None = None) -> ProfileRecord:
        async with self._database.transaction() as connection:
            try:
                profile = cast(
                    Profile,
                    await (
                        self._artifacts.latest(connection, target.scope_id, PROFILE_FAMILY, target.artifact_id)
                        if revision is None
                        else self._artifacts.get(
                            connection,
                            target.scope_id,
                            ArtifactRef(family=PROFILE_FAMILY, artifact_id=target.artifact_id, revision=revision),
                        )
                    ),
                )
            except RepositoryNotFoundError:
                raise BaseValueNotFoundError(
                    "artifact", (target.scope_id, PROFILE_FAMILY, target.artifact_id)
                ) from None
            generation = await self._metadata.revision_metadata(connection, target.scope_id, profile.as_ref())
            if generation is None:
                raise SubjectRootInvariantError("Profile revision is missing generation metadata")  # noqa: TRY003
        return ProfileRecord(target=target, profile=profile, generation=generation, etag=_etag(profile.revision))

    async def replace_profile(
        self,
        target: ResolvedProfileTarget,
        content: str,
        expected_etag: str,
        /,
        *,
        reason: str,
        generation_mode: ProfileGenerationMode = "manual_replace",
        restored_from_revision: int | None = None,
    ) -> ProfileRecord:
        _reason(reason)
        profile_content = ProfileContent(content=content)
        async with self._database.transaction() as connection:
            try:
                current = cast(
                    Profile,
                    await self._artifacts.latest(connection, target.scope_id, PROFILE_FAMILY, target.artifact_id),
                )
            except RepositoryNotFoundError:
                raise BaseValueNotFoundError(
                    "artifact", (target.scope_id, PROFILE_FAMILY, target.artifact_id)
                ) from None
            current_etag = _etag(current.revision)
            if expected_etag != current_etag:
                raise ArtifactRevisionPreconditionError(expected_etag, current_etag)
            source = _profile_command_source(
                source_id=self._id_factory("source"),
                scope_id=target.scope_id,
                artifact_id=target.artifact_id,
                revision=current.revision + 1,
                operation="artifact_replace",
                content=profile_content,
            )
            stored = await self._sources.add(connection, target.scope_id, source)
            revised = cast(
                Profile,
                await self._artifacts.revise(
                    connection,
                    target.scope_id,
                    current,
                    ProfileDraft(
                        content=profile_content,
                        sources=(stored.ref,),
                        artifacts=(current.as_ref(),),
                    ),
                ),
            )
            generation = await self._metadata.add_revision_metadata(
                connection,
                scope_id=target.scope_id,
                artifact_ref=revised.as_ref(),
                generation_mode=generation_mode,
                operation_reason=reason,
                restored_from_revision=restored_from_revision,
            )
        return ProfileRecord(target=target, profile=revised, generation=generation, etag=_etag(revised.revision))

    async def rollback_profile(
        self,
        target: ResolvedProfileTarget,
        restore_revision: int,
        expected_etag: str,
        /,
        *,
        reason: str,
    ) -> ProfileRecord:
        restored = await self.get_profile(target, revision=restore_revision)
        return await self.replace_profile(
            target,
            restored.profile.content.content,
            expected_etag,
            reason=reason,
            generation_mode="rollback",
            restored_from_revision=restore_revision,
        )


def _profile_command_source(
    *,
    source_id: str,
    scope_id: str,
    artifact_id: str,
    revision: int,
    operation: str,
    content: ProfileContent,
) -> ContentSource:
    wire_content = cast(dict[str, JsonValue], content.model_dump(mode="json", by_alias=True))
    return ContentSource(
        name=source_id,
        materialization=SourceMaterialization.CAPTURED,
        content=content.content,
        wire_content=wire_content,
        wire_content_present=True,
        internal=ContentSourceInternal(
            role="lineage_only",
            operation=cast(Any, operation),
            target=ContentSourceTarget(
                scope_id=scope_id,
                family=PROFILE_FAMILY,
                artifact_id=artifact_id,
                revision=revision,
            ),
        ),
    )


def _subject_target(subject: SubjectRoot) -> ResolvedProfileTarget:
    return ResolvedProfileTarget(
        scope_id=subject.root_scope_id,
        artifact_id=USER_PROFILE_ARTIFACT_ID,
        subject_key=subject.subject_key,
        root_scope_id=subject.root_scope_id,
    )


def _subject_key(value: str) -> str:
    if not value or not value.strip() or len(value) > 256:
        raise InvalidBaseAccessRequestError("subject_key", "must be a non-blank string of at most 256 characters")
    return value


def _reason(value: str) -> str:
    if not value.strip():
        raise InvalidBaseAccessRequestError("reason", "must not be blank")
    return value


def _source_content_digest(source: Source) -> str:
    if isinstance(source, ContentSource):
        value: JsonValue = source.wire_content if source.wire_content_present else source.content
    else:
        value = cast(JsonValue, source.model_dump(mode="json", by_alias=True, exclude_none=True))
    return f"sha256:{hashlib.sha256(rfc8785.dumps(cast(Any, value))).hexdigest()}"


def _resource_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _etag(revision: int) -> str:
    return f'"revision:{revision}"'


__all__ = [
    "RelationalProfileService",
    "SubjectRootInvariantError",
    "SubjectRootNotFoundError",
]
