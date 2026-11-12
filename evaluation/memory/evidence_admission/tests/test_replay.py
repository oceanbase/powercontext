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
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.sources import SourceRef

from powercontext_eval_evidence_admission.models import digest
from powercontext_eval_evidence_admission.replay import replay_case


@pytest.mark.parametrize("case_id", ["small-complete", "identity-replay", "observation-overflow"])
def test_real_capture_reopen_projection_and_retry_are_identical(suite, case_id):
    case = next(case for case in suite.cases if case.case_id == case_id)

    async def scenario():
        hashes = []
        for _ in range(2):
            async with replay_case(case) as replay:
                assert tuple(item.journal_position for item in replay.value.evidence) == tuple(
                    range(1, len(case.sources) + 1)
                )
                assert await replay.mutations() == (0, 0)
                hashes.append((replay.materializations, digest(replay.value.model_dump(mode="json"))))
        assert hashes[0] == hashes[1]

    asyncio.run(scenario())


def test_identical_source_id_in_other_scope_cannot_read_fixture(small_case):
    async def scenario():
        async with replay_case(small_case) as replay, replay.contexts.database.transaction() as connection:
            with pytest.raises(RepositoryNotFoundError):
                await replay.sources.get(
                    connection, "unrelated-scope", SourceRef(source_type="content", source_id="small")
                )

    asyncio.run(scenario())
