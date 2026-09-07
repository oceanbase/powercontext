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

import asyncio
from itertools import count

import pytest
from sqlalchemy import func, select

from powercontext.builtin.artifacts.profile import (
    PROFILE_SOURCE_WINDOW_BINDING,
    Profile,
)
from powercontext.builtin.artifacts.profile.service import RelationalProfileService
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import (
    ARTIFACT_PROCESSING_PENDING_TABLE,
    ARTIFACTS_TABLE,
    BUILTIN_TABLES,
    PROFILE_POLICIES_TABLE,
    PROFILE_REVISION_METADATA_TABLE,
    SOURCES_TABLE,
    SUBJECT_ROOTS_TABLE,
    SUBJECT_SOURCE_PROJECTIONS_TABLE,
)
from powercontext.builtin.records import ArtifactRevisionPreconditionError
from powercontext.builtin.scope import ScopeApplication, ScopeDraft
from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY, ContentSource
from powercontext.sources import SourceMaterialization


def _service(profile: SQLiteProfile) -> RelationalProfileService:
    sequence = count(1)
    sources = SourceRepository(BUILTIN_SOURCE_REGISTRY)
    artifacts = ArtifactRepository((Profile,), sources=sources)
    return RelationalProfileService(
        profile.database,
        sources,
        artifacts,
        id_factory=lambda prefix: f"{prefix}-{next(sequence)}",
    )


def test_subject_root_resolution_is_stable_and_creates_default_policy() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            service = _service(profile)

            first, first_created = await service.resolve_subject("user-10086")
            second, second_created = await service.resolve_subject("user-10086")

            assert first == second
            assert first_created is True
            assert second_created is False
            async with profile.database.transaction() as connection:
                assert await connection.scalar(select(func.count()).select_from(SUBJECT_ROOTS_TABLE)) == 1
                policy = (
                    (
                        await connection.execute(
                            select(PROFILE_POLICIES_TABLE).where(
                                PROFILE_POLICIES_TABLE.c.scope_id == first.root_scope_id
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                assert policy["generation_enabled"] is True
                assert policy["activation_mode"] == "automatic"
                scope = await ScopeApplication(profile.database).get(first.root_scope_id)
                assert scope.parent_scope_id is None

    asyncio.run(scenario())


def test_subject_source_is_committed_once_per_origin_and_root() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            scopes = ScopeApplication(profile.database)
            origin = await scopes.create(
                ScopeDraft(title="Group", summary="Shared group chat", idempotency_key="group")
            )
            service = _service(profile)
            source = ContentSource(
                name="message-1",
                materialization=SourceMaterialization.CAPTURED,
                content="我偏好中文简洁回答。",
            )

            first = await service.route_source(origin.scope_id, "user-10086", source)
            second = await service.route_source(origin.scope_id, "user-10086", source)

            assert second == first
            assert first.origin_ref == first.root_ref
            assert first.origin_position == first.root_position == 1
            assert first.subject.root_scope_id != origin.scope_id
            async with profile.database.transaction() as connection:
                assert await connection.scalar(select(func.count()).select_from(SOURCES_TABLE)) == 2
                assert await connection.scalar(select(func.count()).select_from(SUBJECT_SOURCE_PROJECTIONS_TABLE)) == 1
                pending = (
                    (
                        await connection.execute(
                            select(ARTIFACT_PROCESSING_PENDING_TABLE).where(
                                ARTIFACT_PROCESSING_PENDING_TABLE.c.scope_id == first.subject.root_scope_id
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                assert pending["binding_name"] == PROFILE_SOURCE_WINDOW_BINDING
                assert pending["source_through"] == 1

    asyncio.run(scenario())


def test_manual_profile_create_replace_and_rollback_append_revisions() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            service = _service(profile)
            await service.resolve_subject("user-10086")
            target = await service.resolve_target(subject_key="user-10086")

            created = await service.create_profile(target, "# 用户画像\n\n偏好中文。", reason="initial profile")
            replaced = await service.replace_profile(
                target,
                "# 用户画像\n\n偏好简洁中文。",
                created.etag,
                reason="user correction",
            )
            rolled_back = await service.rollback_profile(
                target,
                1,
                replaced.etag,
                reason="restore the initial wording",
            )

            assert (created.profile.revision, replaced.profile.revision, rolled_back.profile.revision) == (1, 2, 3)
            assert rolled_back.profile.content == created.profile.content
            assert rolled_back.generation.generation_mode == "rollback"
            assert rolled_back.generation.restored_from_revision == 1
            assert rolled_back.profile.lineage.artifacts == (replaced.profile.as_ref(),)
            assert all(len(record.profile.lineage.sources) == 1 for record in (created, replaced, rolled_back))

            loaded = await service.get_profile(target)
            assert loaded == rolled_back
            with pytest.raises(ArtifactRevisionPreconditionError):
                await service.replace_profile(target, "stale", created.etag, reason="stale write")
            async with profile.database.transaction() as connection:
                assert await connection.scalar(select(func.count()).select_from(ARTIFACTS_TABLE)) == 3
                assert await connection.scalar(select(func.count()).select_from(PROFILE_REVISION_METADATA_TABLE)) == 3

    asyncio.run(scenario())


def test_root_scope_selector_resolves_to_the_same_user_profile() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            service = _service(profile)
            root, _ = await service.resolve_subject("user-10086")

            by_subject = await service.resolve_target(subject_key="user-10086")
            by_scope = await service.resolve_target(scope_id=root.root_scope_id)

            assert by_scope == by_subject

    asyncio.run(scenario())
