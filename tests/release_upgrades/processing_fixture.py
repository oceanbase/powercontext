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

"""Exercise stored invocation format compatibility using each installed SDK.

Run with the selected installed interpreter and -I, with Server processes stopped.
No schema creation or direct SQL writes: the installed repository owns its rows.
"""

import asyncio
import json
import sys
from importlib.metadata import version
from pathlib import Path

from powercontext.builtin.persistence.oceanbase import OceanBaseProfile
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.seekdb import SeekDBProfile
from powercontext.builtin.persistence.sqlite import SQLiteProfile
from powercontext.server.settings import ServerSettings


async def main():
    operation, scope = sys.argv[1:3]
    source_version = sys.argv[4]
    assert source_version in {"1.3.0", "1.4.0"}, "Unreviewed source processing contract"
    if operation == "seed":
        assert version("powercontext") == source_version, "Seed must use the exact installed source SDK"
    config = ServerSettings().database
    if config.kind == "sqlite":
        storage_context = SQLiteProfile.open(config, tables=())
    elif config.kind == "seekdb":
        storage_context = SeekDBProfile.open(config, tables=())
    else:
        storage_context = OceanBaseProfile.open(config, tables=())
    repository = ArtifactProcessingIntentRepository()
    binding = "topic-memory-source-window"
    async with storage_context as storage, storage.database.transaction() as connection:
        if operation == "seed":
            row = await repository.request(connection, scope, binding)
            if source_version == "1.4.0":
                # Preserve a nondefault field absent from the 1.3.0 task contract.
                await repository.acknowledge(connection, scope, binding, row.requested_generation, dream=True)
                row = await repository.request(connection, scope, binding)
                assert row.consecutive_dream_attempts == 1
            result = row.model_dump(mode="json")
        else:
            row = await repository.load(connection, scope, binding)
            assert row is not None and row.requested_generation > row.handled_generation
            before = row.model_dump(mode="json")
            after = await repository.acknowledge(connection, scope, binding, row.requested_generation)
            repeated = await repository.acknowledge(connection, scope, binding, row.requested_generation)
            assert after == repeated, "Acknowledgment must be idempotent"
            result = {"before": before, "after": after.model_dump(mode="json")}
    return result


if __name__ == "__main__":
    Path(sys.argv[3]).write_text(json.dumps(asyncio.run(main()), indent=2) + "\n", encoding="utf-8")
