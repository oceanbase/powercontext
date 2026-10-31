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

"""Assembly cleanup removes reviewed artifacts only from its owned scopes."""

import asyncio

from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.scope import ScopeDraft
from tests.e2e.real_experience_skill.test_context_text_assembly import _cleanup
from tests.e2e.test_catalog_changes import seed


def test_cleanup_removes_reviewed_artifacts_and_preserves_other_scopes(migration_backend):
    database = migration_backend.config

    async def scenario():
        async with seed(database) as (contexts, scope, source, artifact):
            review = contexts.review(scope)
            candidate = await review.propose_experience(
                artifact.content, sources=(source,), artifacts=(), target=None, reason="Verified evidence"
            )
            await review.approve(candidate.candidate_id, 1)
            keep_scope = (
                await contexts.scopes.create(
                    ScopeDraft(title="Preserved", summary="Unrelated evidence", idempotency_key="keep")
                )
            ).scope_id
            kept = await contexts.records.create_artifact(
                keep_scope, "experience", ArtifactWrite(content=artifact.content.model_dump(mode="json"))
            )
            original = await contexts.records.get_artifact_revision(
                keep_scope, kept.family, kept.artifact_id, kept.revision
            )

        assert await _cleanup(database, [scope]) == {"scope_count": 1, "remaining_rows": 0}
        assert await _cleanup(database, [scope]) == {"scope_count": 1, "remaining_rows": 0}
        async with open_builtin_contexts(BuiltinConfig(database=database)) as contexts:
            assert (
                await contexts.records.get_artifact_revision(keep_scope, kept.family, kept.artifact_id, kept.revision)
                == original
            )

    asyncio.run(scenario())
