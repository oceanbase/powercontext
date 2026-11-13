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

"""Concurrent captures retain independent durable outcomes and cancellation."""

import asyncio
from contextlib import asynccontextmanager

import pytest

from powercontext.builtin.persistence.errors import InvalidStoredPayloadError
from powercontext.builtin.persistence.records import RelationalRecordService
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import InvalidBaseAccessRequestError
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, SourceReceipt, open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft
from powercontext.errors import SourceConflictError


def test_concurrent_captures_preserve_scope_identity_replays_and_individual_conflicts() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            assert runtime.scopes is not None
            scopes = [
                await runtime.scopes.create(
                    ScopeDraft(title=f"Scope {index}", summary="Concurrent captures", idempotency_key=f"scope-{index}")
                )
                for index in range(2)
            ]

            async def capture_scope(scope):
                sources = runtime.sources.for_scope(scope.scope_id)
                values = [
                    CaptureSource(source_id=f"source-{index}", content=f"{scope.scope_id} {index}", metadata={})
                    for index in range(40)
                ]
                retained = await sources.capture(values[0])
                invalid = CaptureSource(source_id="invalid", content="invalid metadata", metadata={"integer": 2**64})
                requests = [
                    values[0],
                    invalid,
                    *values[1:],
                    values[0],
                    CaptureSource(source_id="source-0", content="conflict", metadata={}),
                ]
                outcomes = await asyncio.gather(*(sources.capture(value) for value in requests), return_exceptions=True)
                assert outcomes[0] == outcomes[-2] == retained
                assert isinstance(outcomes[1], InvalidStoredPayloadError)
                assert isinstance(outcomes[-1], SourceConflictError)
                receipts = []
                for outcome in (outcomes[0], *outcomes[2:41]):
                    assert isinstance(outcome, SourceReceipt)
                    receipts.append(outcome)
                assert sorted(outcome.sequence for outcome in receipts) == list(range(1, 41))
                records = runtime.records.for_scope(scope.scope_id)
                page = await records.list_sources(limit=100, cursor=None)
                assert len(page.items) == 40
                for value, receipt in zip(values, receipts, strict=True):
                    record = await records.get_source("content", value.source_id)
                    assert record.position == receipt.sequence
                    assert record.content == value.content

            await asyncio.gather(*(capture_scope(scope) for scope in scopes))

    asyncio.run(scenario())


def test_capture_keeps_invalid_input_error_contract() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Input validation", summary="Individual input errors", idempotency_key="validation")
            )
            sources = runtime.sources.for_scope(scope.scope_id)
            with pytest.raises(InvalidBaseAccessRequestError):
                await sources.capture(CaptureSource(source_id="blank", content=" ", metadata={}))
            receipt = await sources.capture(CaptureSource(source_id="valid", content="Valid capture", metadata={}))
            assert receipt.sequence == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("after_commit", [False, True])
def test_cancelling_capture_owner_keeps_other_accepted_requests_replayable(  # noqa: C901 -- Exercise both sides of commit.
    monkeypatch, after_commit: bool
) -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Cancellation", summary="Independent captures", idempotency_key="cancellation")
            )
            sources = runtime.sources.for_scope(scope.scope_id)
            service = runtime._records()
            assert isinstance(service, RelationalRecordService)
            original_capture = service.capture_sources
            original_transaction = service._database.transaction
            original_queue = runtime._source_captures.capture
            head_entered, release_head, all_entered, commit_boundary = (asyncio.Event() for _ in range(4))
            block_commit = False
            entered = 0

            async def observed_capture(*args):
                nonlocal entered
                entered += 1
                if entered == 12:
                    all_entered.set()
                return await original_queue(*args)

            async def gated_capture(scope_id, values):
                nonlocal block_commit
                if values[0].source_id == "head":
                    head_entered.set()
                    await release_head.wait()
                block_commit = any(value.source_id == "cancelled-owner" for value in values)
                try:
                    return await original_capture(scope_id, values)
                finally:
                    block_commit = False

            @asynccontextmanager
            async def gated_transaction(**kwargs):
                async with original_transaction(**kwargs) as connection:
                    yield connection
                    if block_commit and not after_commit:
                        commit_boundary.set()
                        await asyncio.Event().wait()
                if block_commit and after_commit:
                    commit_boundary.set()
                    await asyncio.Event().wait()

            monkeypatch.setattr(runtime._source_captures, "capture", observed_capture)
            monkeypatch.setattr(service, "capture_sources", gated_capture)
            monkeypatch.setattr(service._database, "transaction", gated_transaction)

            def submit(identity):
                return asyncio.create_task(
                    sources.capture(CaptureSource(source_id=identity, content=identity, metadata={}))
                )

            head = submit("head")
            await asyncio.wait_for(head_entered.wait(), 5)
            owner = submit("cancelled-owner")
            followers = [submit(f"follower-{index}") for index in range(10)]
            await asyncio.wait_for(all_entered.wait(), 5)
            release_head.set()
            await asyncio.wait_for(commit_boundary.wait(), 5)
            owner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await owner
            receipts = await asyncio.wait_for(asyncio.gather(head, *followers), 5)
            assert len({receipt.sequence for receipt in receipts}) == 11
            page = await runtime.records.for_scope(scope.scope_id).list_sources(limit=100, cursor=None)
            assert len(page.items) == (12 if after_commit else 11)
            for receipt in receipts:
                record = await runtime.records.for_scope(scope.scope_id).get_source(
                    "content", receipt.source_ref.source_id
                )
                assert record.position == receipt.sequence
            assert sorted(record.position for record in page.items) == list(range(1, len(page.items) + 1))

    asyncio.run(scenario())


def test_close_waits_for_queued_captures(monkeypatch) -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Shutdown", summary="Drain admitted captures", idempotency_key="shutdown")
            )
            sources = runtime.sources.for_scope(scope.scope_id)
            service = runtime._records()
            original_capture = service.capture_sources
            original_queue = runtime._source_captures.capture
            entered, queued, release = (asyncio.Event() for _ in range(3))
            admissions = 0

            async def gated_capture(scope_id, values):
                entered.set()
                await release.wait()
                return await original_capture(scope_id, values)

            async def observed_capture(*args):
                nonlocal admissions
                admissions += 1
                if admissions == 2:
                    queued.set()
                return await original_queue(*args)

            monkeypatch.setattr(service, "capture_sources", gated_capture)
            monkeypatch.setattr(runtime._source_captures, "capture", observed_capture)
            first = asyncio.create_task(sources.capture(CaptureSource(source_id="first", content="first", metadata={})))
            await asyncio.wait_for(entered.wait(), 5)
            second = asyncio.create_task(
                sources.capture(CaptureSource(source_id="second", content="second", metadata={}))
            )
            await asyncio.wait_for(queued.wait(), 5)
            closing = asyncio.create_task(runtime.close())
            await asyncio.sleep(0)
            assert not closing.done()
            release.set()
            receipts = await asyncio.wait_for(asyncio.gather(first, second), 5)
            await asyncio.wait_for(closing, 5)
            assert [receipt.sequence for receipt in receipts] == [1, 2]
            with pytest.raises(RuntimeError, match="closed"):
                await sources.capture(CaptureSource(source_id="late", content="late", metadata={}))

    asyncio.run(scenario())
