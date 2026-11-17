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
import copy
import json

import pytest
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.tables import SOURCES_TABLE
from powercontext.sources import SourceRef
from sqlalchemy import select

from powercontext_eval_evidence_admission.manifests import EvaluationError
from powercontext_eval_evidence_admission.models import digest
from powercontext_eval_evidence_admission.replay import MATERIALIZATION_PROTOCOL, materialization_hash, replay_case


@pytest.fixture(params=["small-complete", "observation-overflow"])
def persisted_envelope(suite, request):
    case = next(case for case in suite.cases if case.case_id == request.param)

    async def read():
        async with replay_case(case) as replay, replay.contexts.database.transaction() as connection:
            payload = await connection.scalar(select(SOURCES_TABLE.c.payload))
            return json.loads(payload)

    return asyncio.run(read())


def test_material_identity_excludes_only_server_write_time(persisted_envelope):
    original = materialization_hash(json.dumps(persisted_envelope).encode())
    assert len(original) == 64
    for write_time in ["2026-01-01T00:00:00Z", "2026-12-01T00:00:00Z", None]:
        changed = {**persisted_envelope, "saved_at": write_time}
        assert materialization_hash(json.dumps(changed).encode()) == original
    without_time = {k: v for k, v in persisted_envelope.items() if k != "saved_at"}
    assert materialization_hash(json.dumps(without_time).encode()) == original
    # The protocol is part of the digest; old raw-envelope hashes are never silently reused.
    assert original == digest({"protocol": MATERIALIZATION_PROTOCOL, "envelope": without_time})


@pytest.mark.parametrize("field", ["content", "metadata", "effective_at", "saved_at", "source_keys"])
def test_material_identity_detects_persisted_changes(persisted_envelope, field):
    changed = copy.deepcopy(persisted_envelope)
    if field == "source_keys":
        changed["source_keys"] = {"source_id": "changed-alias"}
    else:
        # Even a business field named saved_at is material when nested inside the source value.
        changed["value"][field] = "changed material é\r\n"
    assert materialization_hash(json.dumps(changed).encode()) != materialization_hash(
        json.dumps(persisted_envelope).encode()
    )


@pytest.mark.parametrize(
    "payload",
    [
        b"{broken",
        b"[]",
        b'{"encoding":"unknown","representation":"native","value":{}}',
        b'{"encoding":"powercontext-source-v1","representation":"future","value":{}}',
        b'{"encoding":"powercontext-source-v1","representation":"native","value":{},"new_server_field":1}',
    ],
)
def test_unsupported_persistence_format_fails_closed(payload):
    with pytest.raises(EvaluationError, match="unsupported_persisted_materialization"):
        materialization_hash(payload)


@pytest.mark.parametrize("route", ["native", "observation"])
@pytest.mark.parametrize("change", ["body", "metadata", "business_saved_at"])
def test_real_recapture_detects_source_material_changes(small_case, route, change):
    source = small_case.sources[0].model_copy(update={"route": route})
    baseline = small_case.model_copy(update={"sources": (source,)})
    if change == "body":
        changed_source = source.model_copy(
            update={"pieces": (source.pieces[0].model_copy(update={"text": "Changed."}),)}
        )
    else:
        key = "saved_at" if change == "business_saved_at" else "synthetic_tag"
        changed_source = source.model_copy(update={"metadata": {**source.metadata, key: "2026-01-01"}})
    changed = baseline.model_copy(update={"sources": (changed_source,)})

    async def scenario():
        async with replay_case(baseline) as first:
            expected = first.materializations
        async with replay_case(changed) as second:
            assert second.materializations != expected

    asyncio.run(scenario())


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
