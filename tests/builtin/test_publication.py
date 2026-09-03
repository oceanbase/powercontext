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

from __future__ import annotations

import asyncio

import pytest

from powercontext.artifacts import ArtifactAddress, ArtifactRef
from powercontext.builtin.artifacts.experience import ExperienceContent, ExperienceDraft
from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.experience_index import NoExperienceIndex
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.builtin.publication import (
    ArtifactPublicationApplication,
    ArtifactPublicationConflictError,
    ArtifactPublicationRequest,
    ArtifactPublicationUnsupportedError,
)
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.scope import ScopeApplication, ScopeDraft
from powercontext.errors import ArtifactNotFoundError
from tests.builtin.persistence.contract import Report, ReportContent, ReportDraft


class _IndexUnavailableError(RuntimeError):
    pass


class _FailingExperienceIndex:
    async def initialize(self, _connection, /) -> None:
        pass

    async def replace(self, _connection, _scope_id, _experience, /) -> None:
        raise _IndexUnavailableError

    async def search(self, _connection, _scope_id, _query, _limit, /):
        return ()

    async def replace_skill(self, _connection, _scope_id, _skill, _package, /) -> None:
        pass

    async def search_skills(self, _connection, _scope_id, _query, _limit, /):
        return ()


def test_publication_copies_one_exact_revision_with_original_provenance(tmp_path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'publication.db'}")
        async with SQLiteProfile.open(config, tables=BUILTIN_TABLES) as profile:
            scopes = ScopeApplication(profile.database)
            source_scope = await scopes.create(
                ScopeDraft(title="Source", summary="Private working state", idempotency_key="source")
            )
            target_scope = await scopes.create(
                ScopeDraft(title="Target", summary="Accepted result", idempotency_key="target")
            )
            relay_scope = await scopes.create(
                ScopeDraft(title="Relay", summary="Relayed result", idempotency_key="relay")
            )
            artifacts = ArtifactRepository((Report,))
            publications = (
                ArtifactPublicationApplication(
                    profile.database,
                    artifacts,
                    scopes,
                    experience_index=NoExperienceIndex(),
                ),
                ArtifactPublicationApplication(
                    profile.database,
                    artifacts,
                    scopes,
                    experience_index=NoExperienceIndex(),
                ),
            )
            async with profile.database.transaction() as connection:
                first = await artifacts.create(
                    connection,
                    source_scope.scope_id,
                    "report",
                    ReportDraft(content=ReportContent(status="ready")),
                )

            request = ArtifactPublicationRequest(
                source=ArtifactAddress(scope_id=source_scope.scope_id, artifact=first.as_ref()),
                target_scope_id=target_scope.scope_id,
                idempotency_key="accepted-report",
            )
            results = await asyncio.gather(*(publications[index % 2].publish(request) for index in range(8)))
            published = results[0]
            assert all(result == published for result in results)

            async with profile.database.transaction() as connection:
                revised = await artifacts.revise(
                    connection,
                    source_scope.scope_id,
                    first,
                    ReportDraft(content=ReportContent(status="changed")),
                )
                copied = await artifacts.get(connection, published.target.scope_id, published.target.artifact)

            assert revised.revision == 2
            assert copied.content == first.content
            assert copied.lineage.publication_source == request.source
            assert copied.lineage.publication_digest == published.content_digest

            relayed = await publications[0].publish(
                ArtifactPublicationRequest(
                    source=published.target,
                    target_scope_id=relay_scope.scope_id,
                    idempotency_key="relayed-report",
                )
            )
            async with profile.database.transaction() as connection:
                relay_copy = await artifacts.get(connection, relayed.target.scope_id, relayed.target.artifact)

            assert relay_copy.lineage.publication_source == request.source
            assert relay_copy.lineage.publication_digest == published.content_digest

    asyncio.run(scenario())


def test_published_experience_is_searchable_in_target_scope_immediately() -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            source_scope = await contexts.scopes.create(
                ScopeDraft(title="Source", summary="Private working state", idempotency_key="experience-source")
            )
            target_scope = await contexts.scopes.create(
                ScopeDraft(title="Target", summary="Accepted result", idempotency_key="experience-target")
            )
            async with contexts.database.transaction() as connection:
                source = await contexts.repositories.artifacts.create(
                    connection,
                    source_scope.scope_id,
                    "published-experience",
                    ExperienceDraft(
                        content=ExperienceContent(
                            situation="A publication crosses a Scope boundary.",
                            action="Copy the exact approved revision and its search projection.",
                            outcome="The target can recall the Experience immediately.",
                            lesson="Keep publication projection updates atomic.",
                        )
                    ),
                )

            published = await contexts.publications.publish(
                ArtifactPublicationRequest(
                    source=ArtifactAddress(scope_id=source_scope.scope_id, artifact=source.as_ref()),
                    target_scope_id=target_scope.scope_id,
                    idempotency_key="publish-experience",
                )
            )
            hits = await contexts.search_experience(target_scope.scope_id, "projection updates atomic", 8)

            assert tuple(hit.artifact_ref for hit in hits) == (published.target.artifact,)

    asyncio.run(scenario())


def test_experience_publication_rolls_back_when_projection_update_fails() -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            source_scope = await contexts.scopes.create(
                ScopeDraft(title="Source", summary="Private working state", idempotency_key="rollback-source")
            )
            target_scope = await contexts.scopes.create(
                ScopeDraft(title="Target", summary="Accepted result", idempotency_key="rollback-target")
            )
            async with contexts.database.transaction() as connection:
                source = await contexts.repositories.artifacts.create(
                    connection,
                    source_scope.scope_id,
                    "source-experience",
                    ExperienceDraft(
                        content=ExperienceContent(
                            situation="The search projection is unavailable.",
                            action="Abort the publication transaction.",
                            outcome="No partial target lifecycle remains.",
                            lesson="Keep derived state in the publication transaction.",
                        )
                    ),
                )
            publications = ArtifactPublicationApplication(
                contexts.database,
                contexts.repositories.artifacts,
                contexts.scopes,
                experience_index=_FailingExperienceIndex(),
                id_factory=lambda: "pub_rollback",
            )
            request = ArtifactPublicationRequest(
                source=ArtifactAddress(scope_id=source_scope.scope_id, artifact=source.as_ref()),
                target_scope_id=target_scope.scope_id,
                idempotency_key="rollback-publication",
            )

            with pytest.raises(_IndexUnavailableError):
                await publications.publish(request)

            target = ArtifactAddress(
                scope_id=target_scope.scope_id,
                artifact=ArtifactRef(family="experience", artifact_id="pub_rollback", revision=1),
            )
            assert await publications.get(target) is None
            async with contexts.database.transaction() as connection:
                with pytest.raises(RepositoryNotFoundError):
                    await contexts.repositories.artifacts.get(connection, target.scope_id, target.artifact)

    asyncio.run(scenario())


def test_publication_idempotency_key_cannot_select_another_revision() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            scopes = ScopeApplication(profile.database)
            source_scope = await scopes.create(
                ScopeDraft(title="Source", summary="Private working state", idempotency_key="source")
            )
            target_scope = await scopes.create(
                ScopeDraft(title="Target", summary="Accepted result", idempotency_key="target")
            )
            artifacts = ArtifactRepository((Report,))
            publications = ArtifactPublicationApplication(
                profile.database,
                artifacts,
                scopes,
                experience_index=NoExperienceIndex(),
            )
            async with profile.database.transaction() as connection:
                first = await artifacts.create(
                    connection,
                    source_scope.scope_id,
                    "report",
                    ReportDraft(content=ReportContent(status="ready")),
                )
                second = await artifacts.revise(
                    connection,
                    source_scope.scope_id,
                    first,
                    ReportDraft(content=ReportContent(status="changed")),
                )

            await publications.publish(
                ArtifactPublicationRequest(
                    source=ArtifactAddress(scope_id=source_scope.scope_id, artifact=first.as_ref()),
                    target_scope_id=target_scope.scope_id,
                    idempotency_key="accepted-report",
                )
            )
            with pytest.raises(ArtifactPublicationConflictError):
                await publications.publish(
                    ArtifactPublicationRequest(
                        source=ArtifactAddress(scope_id=source_scope.scope_id, artifact=second.as_ref()),
                        target_scope_id=target_scope.scope_id,
                        idempotency_key="accepted-report",
                    )
                )

    asyncio.run(scenario())


def test_memory_publication_is_rejected_without_target_state() -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            source_scope = await contexts.scopes.create(
                ScopeDraft(title="Source", summary="Private working state", idempotency_key="source")
            )
            target_scope = await contexts.scopes.create(
                ScopeDraft(title="Target", summary="Accepted result", idempotency_key="target")
            )
            source = await contexts.get(source_scope.scope_id)
            memory = await source.artifacts.memory.remember(
                memory=None,
                entries=(MemoryEntryInput(kind="decision", text="Keep publication state complete."),),
                mode="append",
            )
            assert memory is not None
            publications = ArtifactPublicationApplication(
                contexts.database,
                contexts.repositories.artifacts,
                contexts.scopes,
                experience_index=contexts.experience_index,
                id_factory=lambda: "pub_blocked",
            )
            request = ArtifactPublicationRequest(
                source=ArtifactAddress(scope_id=source_scope.scope_id, artifact=memory.as_ref()),
                target_scope_id=target_scope.scope_id,
                idempotency_key="publish-memory",
            )

            for _ in range(2):
                with pytest.raises(ArtifactPublicationUnsupportedError, match="family: memory") as raised:
                    await publications.publish(request)
                assert raised.value.family == "memory"

            target_address = ArtifactAddress(
                scope_id=target_scope.scope_id,
                artifact=ArtifactRef(family="memory", artifact_id="pub_blocked", revision=1),
            )
            assert await publications.get(target_address) is None
            target = await contexts.get(target_scope.scope_id)
            with pytest.raises(ArtifactNotFoundError):
                await target.artifacts.memory.revision(target_address.artifact)

    asyncio.run(scenario())
