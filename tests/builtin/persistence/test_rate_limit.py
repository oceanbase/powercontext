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

from powercontext.builtin.persistence.rate_limit import RateLimitRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import RATE_LIMIT_WINDOWS_TABLE


def test_fixed_window_counter_is_shared_by_repository_callers() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=(RATE_LIMIT_WINDOWS_TABLE,)) as profile:
            repository = RateLimitRepository()
            decisions = []
            for _ in range(3):
                async with profile.database.transaction() as connection:
                    decisions.append(
                        await repository.consume(
                            connection,
                            principal_key="a" * 64,
                            policy_id="api.default",
                            limit=2,
                            window_seconds=60,
                        )
                    )

            assert [decision.allowed for decision in decisions] == [True, True, False]
            assert [decision.remaining for decision in decisions] == [1, 0, 0]
            assert decisions[-1].retry_after_seconds > 0

    asyncio.run(scenario())
