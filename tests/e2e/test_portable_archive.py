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

"""Portable archive acceptance through the public built-in Runtime."""

from __future__ import annotations

import asyncio
from pathlib import Path

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    HandoffDraft,
    HandoffSourceCitation,
    HandoffStatement,
    RememberMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.work import AcknowledgeHandoff, ReceiverChecks


def test_sqlite_portable_archive_restores_revisions_lineage_and_handoff_receipts(tmp_path: Path) -> None:
    async def scenario() -> None:
        archive_path = tmp_path / "portable.pcb"
        source_config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'source.db'}"))
        target_config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'target.db'}"))

        async with open_builtin_runtime(source_config) as source_runtime:
            assert source_runtime.scopes is not None
            scope = await source_runtime.scopes.create(
                ScopeDraft(
                    title="Portable archive", summary="Archive recovery fixture", idempotency_key="portable-archive"
                )
            )
            scope_id = scope.scope_id
            source = await source_runtime.sources.for_scope(scope_id).capture(
                CaptureSource(
                    source_id="turn-1",
                    content="The archive must preserve the handoff citation.",
                    metadata={"origin": "portable-archive-e2e"},
                )
            )
            memory = await source_runtime.memory.for_scope(scope_id).remember(
                RememberMemoryRequest(
                    entries=(MemoryEntryInput(kind="decision", text="Use the portable archive for migration."),)
                )
            )
            assert memory.entry is not None
            handoffs = source_runtime.handoff.for_scope(scope_id)
            prepared = await handoffs.finalize(
                HandoffDraft(
                    objective="Restore an exact logical handoff.",
                    state=(
                        HandoffStatement(
                            text="The source citation is durable.",
                            citations=(HandoffSourceCitation(source_ref=source.source_ref),),
                        ),
                    ),
                    disposition="continuable",
                    next_action=HandoffStatement(
                        text="Open the restored handoff.",
                        citations=(HandoffSourceCitation(source_ref=source.source_ref),),
                    ),
                )
            )
            committed = await handoffs.commit(prepared)
            acknowledgement = await source_runtime.work.for_scope(scope_id).acknowledge(
                AcknowledgeHandoff(
                    source_id="handoff-receipt-1",
                    receiver="portable-target",
                    status="accepted",
                    selection="exact",
                    revision=committed.as_ref(),
                    receiver_checks=ReceiverChecks(
                        live_state="confirmed",
                        capability="confirmed",
                        authorization="confirmed",
                    ),
                )
            )
            assert source_runtime.archive is not None

            async def authorize(scopes: tuple[str, ...]) -> None:
                assert scopes == (scope_id,)

            exported = await source_runtime.archive.export([scope_id], archive_path, authorize=authorize)

        async with open_builtin_runtime(target_config) as target_runtime:
            assert target_runtime.archive is not None
            restored = await target_runtime.archive.restore(archive_path)
            latest = await target_runtime.handoff.for_scope(scope_id).latest()
            continuity = await target_runtime.work.for_scope(scope_id).continuity()

            assert restored.record_count == exported.record_count
            assert restored.projections_ready is True
            assert latest == committed
            assert latest is not None
            assert latest.revision == 1
            assert latest.lineage.sources == (source.source_ref,)
            assert continuity.coverage.acknowledgement_records == 1
            assert continuity.coverage.active_receipt_ref == acknowledgement.receipt.source_ref

    asyncio.run(scenario())
