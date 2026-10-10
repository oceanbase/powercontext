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

"""A registered ordinary Family reuses exact merge relations and group restoration."""

from __future__ import annotations

import asyncio

import pytest

from powercontext.artifacts import ArtifactLineage
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.sources.content import CONTENT_SOURCE_ADAPTER
from tests.builtin.persistence.contract import HandoffContent, HandoffDraft, repository_profile


class _LocalSecurity:
    def subject(self, context):
        return context

    async def lock_transaction(self, connection, scope_id, context):
        pass

    async def authorize(self, connection, scope_id, context, action, ref=None):
        pass

    async def authorize_sources(self, connection, scope_id, context, sources):
        pass

    async def establish_owner(self, connection, scope_id, artifact_id, context):
        pass


class _HandoffAdapter:
    family = "handoff"

    def draft(self, content, lineage, *, merge_inputs=(), historical=False):
        return HandoffDraft(
            content=HandoffContent.model_validate(content), **lineage.model_dump(include={"sources", "artifacts"})
        )

    async def prepare(self, content):
        return content

    def validate_prepared(self, prepared):
        pass

    async def publish(self, connection, scope_id, record, prepared, execution_context):
        pass

    async def remove(self, connection, scope_id, artifact_id):
        pass


async def _merge_tags(connection, scope_id, artifact_id, inputs):
    pass


def test_shared_family_cascade_restores_selected_inputs_and_preserves_ordinary_references() -> None:
    async def scenario() -> None:
        from powercontext.builtin.artifacts.merge import ArtifactMergeService
        from powercontext.builtin.runtime.artifact_merge import ArtifactMergeApplication

        async with repository_profile() as (profile, repositories):
            service = ArtifactMergeService(
                artifacts=repositories.artifacts,
                sources=SourceRepository((CONTENT_SOURCE_ADAPTER,)),
                adapter=_HandoffAdapter(),
                security=_LocalSecurity(),
                merge_tags=_merge_tags,
            )
            scoped = ArtifactMergeApplication(profile.database, (service,), default_context="owner").for_scope(
                "project", "handoff"
            )
            async with profile.database.transaction() as connection:
                for identity in ("a", "b", "d", "evidence"):
                    await repositories.artifacts.create(
                        connection, "project", identity, HandoffDraft(content=HandoffContent(summary=identity))
                    )

            async def merge(result, identities, evidence=()):
                inputs = tuple([(await scoped.get(identity)).as_read() for identity in identities])
                return await scoped.merge(
                    inputs,
                    HandoffContent(summary=result),
                    artifact_id=result,
                    lineage=ArtifactLineage(artifacts=(*evidence, *(read.ref for read in reversed(inputs)))),
                )

            evidence = (await scoped.get("evidence")).ref
            first = await merge("c", ("a", "b"), (evidence,))
            await merge("e", ("c", "d"))
            frozen = await scoped.get("a", revision=1)
            assert frozen.artifact.content.summary == "a"
            assert frozen.state.merged_into_id == "c"
            async with profile.database.transaction() as connection:
                assert await repositories.artifacts.merge_inputs(connection, "project", first.primary.ref) == tuple(
                    reversed(tuple(record.ref for record in first.records if record.ref.artifact_id in {"a", "b"}))
                )
            restored = await scoped.restore("b")
            assert restored.undo_merge_results == ("e", "c")
            assert {ref.artifact_id: ref.revision for ref in restored.restored} == {"a": 2, "b": 2, "d": 2}
            assert {ref.artifact_id: ref.revision for ref in restored.retired} == {"c": 2, "e": 2}
            for identity in ("a", "b", "d", "evidence"):
                current = await scoped.get(identity)
                assert current.state.lifecycle_state == "active"
                assert current.state.merged_into_id is None
                if identity != "evidence":
                    assert current.artifact.lineage.artifacts == ((await scoped.get(identity, revision=1)).ref,)
            for identity in ("c", "e"):
                current = await scoped.get(identity)
                assert current.state.lifecycle_state == "retired"
                historical = await scoped.get(identity, revision=1)
                assert current.artifact.content == historical.artifact.content
                assert current.artifact.lineage.artifacts == (historical.ref,)
                async with profile.database.transaction() as connection:
                    assert await repositories.artifacts.merge_inputs(connection, "project", current.ref) == ()
            async with profile.database.transaction() as connection:
                assert await repositories.artifacts.merge_inputs(connection, "project", first.primary.ref) == tuple(
                    reversed(tuple(record.ref for record in first.records if record.ref.artifact_id in {"a", "b"}))
                )
            assert (await scoped.get("evidence")).state.governance_generation == 0

    asyncio.run(scenario())


def test_shared_active_restore_is_noop_unless_a_revision_is_selected() -> None:
    async def scenario() -> None:
        from powercontext.builtin.artifacts.merge import ArtifactMergeService
        from powercontext.builtin.artifacts.merge_restoration import ArtifactMergePreviewSigner
        from powercontext.builtin.runtime.artifact_merge import ArtifactMergeApplication

        async with repository_profile() as (profile, repositories):
            service = ArtifactMergeService(
                artifacts=repositories.artifacts,
                sources=SourceRepository((CONTENT_SOURCE_ADAPTER,)),
                adapter=_HandoffAdapter(),
                security=_LocalSecurity(),
                merge_tags=_merge_tags,
                preview_signer=ArtifactMergePreviewSigner(keys={"test": b"x" * 32}, active_key_id="test"),
            )
            scoped = ArtifactMergeApplication(profile.database, (service,), default_context="owner").for_scope(
                "project", "handoff"
            )
            async with profile.database.transaction() as connection:
                original = await repositories.artifacts.create(
                    connection, "project", "original", HandoffDraft(content=HandoffContent(summary="original"))
                )
            preview = await scoped.preview_restoration("original")
            assert len(preview.restore) == 1 and not preview.restore[0].creates_revision
            unchanged = await scoped.restore("original", preview_token=preview.preview_token)
            assert not unchanged.changed
            assert unchanged.primary.artifact == original
            selected = await scoped.preview_restoration("original", revision=1)
            assert selected.restore[0].creates_revision
            restored = await scoped.restore("original", revision=1, preview_token=selected.preview_token)
            assert restored.changed and restored.primary.ref.revision == 2
            assert restored.primary.artifact.content == original.content
            assert restored.primary.artifact.lineage.artifacts == (original.as_ref(),)
            earlier = await scoped.restore("original", revision=1)
            assert earlier.primary.ref.revision == 3
            assert earlier.primary.artifact.lineage.artifacts == (restored.primary.ref, original.as_ref())
            assert (await scoped.get("original", revision=1)).artifact == original
            selected_receipt = await scoped.restore("original", revision=2)
            assert selected_receipt.primary.ref.revision == 4
            assert selected_receipt.primary.artifact.lineage.artifacts == (earlier.primary.ref, restored.primary.ref)
            assert len(selected_receipt.primary.artifact.lineage.sources) == 1
            assert selected_receipt.primary.artifact.lineage.sources != restored.primary.artifact.lineage.sources
            outcome = await scoped.restoration_outcome("original", revision=4)
            assert outcome is not None and outcome.writes[0].content_from_ref == restored.primary.ref

    asyncio.run(scenario())


def test_shared_restoration_publication_failure_rolls_back_all_appended_revisions() -> None:
    async def scenario() -> None:
        from powercontext.builtin.artifacts.merge import ArtifactMergeService
        from powercontext.builtin.persistence.errors import RepositoryNotFoundError
        from powercontext.builtin.runtime.artifact_merge import ArtifactMergeApplication

        class RejectPublication(_HandoffAdapter):
            reject = False

            async def publish(self, connection, scope_id, record, prepared, execution_context):
                if self.reject and record.ref.artifact_id == "b":
                    raise ValueError("publication unavailable")  # noqa: TRY003

        async with repository_profile() as (profile, repositories):
            adapter = RejectPublication()
            service = ArtifactMergeService(
                artifacts=repositories.artifacts,
                sources=SourceRepository((CONTENT_SOURCE_ADAPTER,)),
                adapter=adapter,
                security=_LocalSecurity(),
                merge_tags=_merge_tags,
            )
            scoped = ArtifactMergeApplication(profile.database, (service,), default_context="owner").for_scope(
                "project", "handoff"
            )
            async with profile.database.transaction() as connection:
                for identity in ("a", "b"):
                    await repositories.artifacts.create(
                        connection, "project", identity, HandoffDraft(content=HandoffContent(summary=identity))
                    )
            inputs = tuple([(await scoped.get(identity)).as_read() for identity in ("a", "b")])
            await scoped.merge(inputs, HandoffContent(summary="combined"), artifact_id="c")
            before = {identity: await scoped.get(identity) for identity in ("a", "b", "c")}
            adapter.reject = True
            with pytest.raises(ValueError, match="publication unavailable"):
                await scoped.restore("a")
            for identity, current in before.items():
                assert await scoped.get(identity) == current
                assert (await scoped.get(identity, revision=1)).artifact == current.artifact
                with pytest.raises(RepositoryNotFoundError):
                    await scoped.get(identity, revision=2)
            async with profile.database.transaction() as connection:
                assert service.sources is not None
                assert await service.sources.list(connection, "project") == ()

    asyncio.run(scenario())


def test_shared_ordinary_deprecated_revision_preserves_replacement_until_explicit_restore() -> None:
    async def scenario() -> None:
        from powercontext.builtin.artifacts.merge import ArtifactMergeService
        from powercontext.builtin.persistence.artifact_governance import (
            ArtifactGovernanceRepository,
            ArtifactLifecycleState,
        )

        async with repository_profile() as (profile, repositories):
            service = ArtifactMergeService(
                artifacts=repositories.artifacts,
                sources=SourceRepository((CONTENT_SOURCE_ADAPTER,)),
                adapter=_HandoffAdapter(),
                security=_LocalSecurity(),
                merge_tags=_merge_tags,
            )
            governance = ArtifactGovernanceRepository()
            async with profile.database.transaction() as connection:
                for identity in ("original", "replacement"):
                    await repositories.artifacts.create(
                        connection, "project", identity, HandoffDraft(content=HandoffContent(summary=identity))
                    )
                await governance.transition(
                    connection, "project", "handoff", "original", 0, ArtifactLifecycleState.DEPRECATED, "replacement"
                )
                read = await service.get(connection, "project", "original", "owner")
                assert read.state.replacement_artifact_id == "replacement" and read.state.merged_into_id is None
                plan = await service.inspect_change(
                    connection,
                    "project",
                    "original",
                    HandoffContent(summary="revised"),
                    "owner",
                    expected_revision=1,
                    expected_state_version=1,
                )
            prepared = await service.prepare_change(plan)
            async with profile.database.transaction() as connection:
                result = await service.commit(connection, prepared, "owner")
                assert result.primary.ref.revision == 2
                assert result.primary.state.lifecycle_state == "deprecated"
                assert result.primary.state.replacement_artifact_id == "replacement"
                assert result.primary.state.governance_generation == 1
                plan = await service.inspect_restore(connection, "project", "original", "owner")
            prepared = await service.prepare_restore(plan)
            async with profile.database.transaction() as connection:
                restored = await service.commit(connection, prepared, "owner")
                assert restored.primary.state.lifecycle_state == "active"
                assert restored.primary.state.replacement_artifact_id is None
                assert restored.primary.state.governance_generation == 2
                assert restored.primary.ref.revision == 3
                assert restored.primary.artifact.content == result.primary.artifact.content
                assert restored.primary.artifact.lineage.artifacts == (result.primary.ref,)
                assert (
                    await repositories.artifacts.get(connection, "project", result.primary.ref)
                ) == result.primary.artifact

    asyncio.run(scenario())
