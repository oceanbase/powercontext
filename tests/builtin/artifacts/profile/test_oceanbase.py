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


"""Opt-in Profile contract against a disposable OceanBase test database."""

import asyncio
import os
from uuid import uuid4

import pytest
from pydantic import SecretStr

from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.builtin.scope import ScopeDraft

LIVE_URL = os.environ.get("POWERCONTEXT_TEST_OCEANBASE_URL")


@pytest.mark.skipif(not LIVE_URL, reason="requires a disposable POWERCONTEXT_TEST_OCEANBASE_URL database")
def test_oceanbase_subject_profile_review_contract():
    class Generator:
        async def generate(self, value):
            return "# Profile\n\n- Prefers Chinese."

    async def run():
        assert LIVE_URL is not None
        async with OceanBaseProfile.open(OceanBaseConfig(url=SecretStr(LIVE_URL)), tables=BUILTIN_TABLES) as db:
            contexts = RelationalContexts(database=db.database)
            key = uuid4().hex
            group = await contexts.scopes.create(
                ScopeDraft(title="Profile contract", summary="Test", idempotency_key=key)
            )
            target, pair = await contexts.subject_sources.create(
                group.scope_id, key, {"speaker": key, "text": "Chinese"}
            )
            assert pair[0].source_id == pair[1].source_id
            policy = await contexts.profiles.get_policy(target)
            await contexts.profiles.put_policy(
                target, generation_enabled=True, activation_mode="review_required", expected_version=policy.version
            )
            contexts.profiles.generator = Generator()
            pending = await contexts.profiles.flush(target)
            assert pending.candidate_id is not None
            approved = await contexts.review(target).approve(pending.candidate_id, 1)
            assert approved.result_artifact is not None and approved.result_artifact.artifact_id == "profile"
            assert (await contexts.profiles.flush(target)).status == "noop"

    asyncio.run(run())
