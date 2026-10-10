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

"""Constructor registration admits an ordinary test Family to the public merge API."""

from __future__ import annotations

import asyncio

import pytest

from powercontext.builtin.artifacts.merge import ArtifactMergeService
from powercontext.builtin.artifacts.merge_restoration import ArtifactMergePreviewSigner
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.records import BaseOperationNotSupportedError
from powercontext.builtin.runtime import ArtifactMergeApplication
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


class _TestFamilyAdapter:
    family = "handoff"

    def draft(self, content, lineage, *, merge_inputs=(), historical=False):
        return HandoffDraft(
            content=HandoffContent.model_validate(content), sources=lineage.sources, artifacts=lineage.artifacts
        )

    async def prepare(self, content):
        return content

    def validate_prepared(self, prepared):
        pass

    async def publish(self, connection, scope_id, record, prepared, execution_context):
        pass

    async def remove(self, connection, scope_id, artifact_id):
        pass


async def _tags(connection, scope_id, artifact_id, inputs):
    pass


def test_constructor_registration_reuses_shared_merge_preview_and_restore() -> None:
    async def scenario() -> None:
        async with repository_profile() as (profile, repositories):
            service = ArtifactMergeService(
                artifacts=repositories.artifacts,
                sources=SourceRepository((CONTENT_SOURCE_ADAPTER,)),
                adapter=_TestFamilyAdapter(),
                security=_LocalSecurity(),
                merge_tags=_tags,
                preview_signer=ArtifactMergePreviewSigner(keys={"test": b"x" * 32}, active_key_id="test"),
            )
            application = ArtifactMergeApplication(
                profile.database,
                (service,),
                default_context="local-owner",
                id_factory=lambda family: family + "-result",
            )
            scoped = application.for_scope("project", "handoff")
            with pytest.raises(BaseOperationNotSupportedError):
                application.for_scope("project", "experience")
            async with profile.database.transaction() as connection:
                for identity in ("a", "b"):
                    await repositories.artifacts.create(
                        connection, "project", identity, HandoffDraft(content=HandoffContent(summary=identity))
                    )
            a, b = await scoped.get("a"), await scoped.get("b")
            result = (await scoped.merge((a.as_read(), b.as_read()), HandoffContent(summary="combined"))).primary
            assert result.ref.artifact_id == "handoff-result"
            assert (await scoped.get("a", revision=1)).artifact == a.artifact
            assert (await scoped.get("a")).state.merged_into_id == result.ref.artifact_id
            preview = await scoped.preview_restoration(result.ref.artifact_id, operation="undo_merge")
            restored = await scoped.restore(
                result.ref.artifact_id, operation="undo_merge", preview_token=preview.preview_token
            )
            assert restored.undo_merge_results == (result.ref.artifact_id,)
            for original in (a, b):
                current = await scoped.get(original.ref.artifact_id)
                assert current.artifact.content == original.artifact.content
                assert current.ref.revision == 2
                assert (await scoped.get(original.ref.artifact_id, revision=1)).artifact == original.artifact
            assert (await scoped.get(result.ref.artifact_id)).state.lifecycle_state == "retired"
            assert restored.primary.ref.revision == 2
            outcome = await scoped.restoration_outcome(result.ref.artifact_id, revision=2)
            assert outcome is not None and outcome.undo_merge_results == (result.ref,)

    asyncio.run(scenario())
