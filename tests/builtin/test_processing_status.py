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

"""Read-only processing status inspection model tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, Mock

from powercontext.builtin.persistence.processing_intents import StoredArtifactProcessingIntent
from powercontext.builtin.processing_status import (
    ObservationOrigin,
    ProcessingStatusInspector,
    ProcessingStatusProgress,
    ProcessingStatusState,
    _unsupported_snapshot,
    _utc_now,
)


def _run(async_fn):
    """Helper to run async test functions synchronously."""
    return asyncio.run(async_fn)


def test_progress_pending_inputs_when_head_ahead_of_cursor() -> None:
    progress = ProcessingStatusProgress(source_cursor=10, source_head=15)
    assert progress.pending_inputs is True
    assert progress.outstanding_work is False


def test_progress_no_pending_when_head_equals_cursor() -> None:
    progress = ProcessingStatusProgress(source_cursor=10, source_head=10)
    assert progress.pending_inputs is False


def test_progress_outstanding_work_when_dirty_ahead_of_clean() -> None:
    progress = ProcessingStatusProgress(dirty_generation=5, clean_generation=3)
    assert progress.outstanding_work is True


def test_progress_no_outstanding_work_when_dirty_equals_clean() -> None:
    progress = ProcessingStatusProgress(dirty_generation=5, clean_generation=5)
    assert progress.outstanding_work is False


def test_unsupported_snapshot_marks_unknown() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    result = _unsupported_snapshot("unknown-family", "scope-1", now, "test_reason")

    assert result.family == "unknown-family"
    assert result.state is ProcessingStatusState.UNKNOWN
    assert result.details == {"reason": "test_reason"}
    assert result.observed_at == now


def _make_intent(**overrides: int) -> StoredArtifactProcessingIntent:
    return StoredArtifactProcessingIntent.model_validate({
        "binding_name": overrides.pop("binding_name", "source-window"),
        "scope_id": "scope-1",
        "pending_sequence": 0,
        "dirty_generation": overrides.pop("dirty_generation", 0),
        "clean_generation": overrides.pop("clean_generation", 0),
        "requested_generation": overrides.pop("requested_generation", 0),
        "handled_generation": overrides.pop("handled_generation", 0),
        "last_auto_scan_generation": 0,
        "consecutive_dream_attempts": 0,
        **overrides,
    })


class TestProcessingStatusInspector:
    def test_source_consumption_idle_when_no_intent(self) -> None:
        intents = MagicMock()
        intents.load = AsyncMock(return_value=None)
        inspector = ProcessingStatusInspector(intents=intents)

        result = _run(
            inspector._source_consumption_snapshot(Mock(), "scope-1", "memory", datetime(2026, 1, 1, tzinfo=UTC))
        )

        assert result.state is ProcessingStatusState.IDLE
        assert result.progress is None
        assert result.details["binding"] == "memory-source-window"

    def test_source_consumption_pending_when_inputs_outstanding(self) -> None:
        intent = _make_intent(dirty_generation=15, clean_generation=10)
        intents = MagicMock()
        intents.load = AsyncMock(return_value=intent)
        inspector = ProcessingStatusInspector(intents=intents)

        result = _run(
            inspector._source_consumption_snapshot(Mock(), "scope-1", "memory", datetime(2026, 1, 1, tzinfo=UTC))
        )

        assert result.state is ProcessingStatusState.PENDING
        assert result.progress is not None
        assert result.progress.source_cursor == 10
        assert result.progress.source_head == 15

    def test_source_consumption_idle_when_caught_up(self) -> None:
        intent = _make_intent(dirty_generation=10, clean_generation=10)
        intents = MagicMock()
        intents.load = AsyncMock(return_value=intent)
        inspector = ProcessingStatusInspector(intents=intents)

        result = _run(
            inspector._source_consumption_snapshot(Mock(), "scope-1", "profile", datetime(2026, 1, 1, tzinfo=UTC))
        )

        assert result.state is ProcessingStatusState.IDLE
        assert result.progress is not None
        assert result.progress.pending_inputs is False
        assert result.progress.outstanding_work is False

    def test_topic_memory_unavailable_when_no_status_provider(self) -> None:
        inspector = ProcessingStatusInspector(topic_memory_status=None)

        result = _run(inspector._topic_memory_snapshot(Mock(), "scope-1", datetime(2026, 1, 1, tzinfo=UTC)))

        assert result.state is ProcessingStatusState.UNKNOWN
        assert result.details["reason"] == "recovery_not_configured"

    def test_topic_memory_graceful_on_status_provider_error(self) -> None:
        failing_status = AsyncMock(side_effect=RuntimeError("boom"))
        inspector = ProcessingStatusInspector(topic_memory_status=failing_status)

        result = _run(inspector._topic_memory_snapshot(Mock(), "scope-1", datetime(2026, 1, 1, tzinfo=UTC)))

        assert result.state is ProcessingStatusState.UNKNOWN
        assert result.details["reason"] == "status_query_failed"

    def test_topic_memory_maps_idle_state(self) -> None:
        raw = Mock(state=Mock(value="idle"), block=None, source_cursor=5, source_head=10, target_source_through=None)
        status_fn = AsyncMock(return_value=raw)
        inspector = ProcessingStatusInspector(topic_memory_status=status_fn)

        result = _run(inspector._topic_memory_snapshot(Mock(), "scope-1", datetime(2026, 1, 1, tzinfo=UTC)))

        assert result.state is ProcessingStatusState.IDLE
        assert result.progress is not None
        assert result.progress.source_cursor == 5
        assert result.progress.source_head == 10

    def test_topic_memory_maps_blocked_state_with_diagnostics(self) -> None:
        raw = Mock(
            state=Mock(value="blocked"),
            block=Mock(block_id="b1", stage="topic_memory", error_code="window_attempt_limit"),
            source_cursor=3,
            source_head=5,
            target_source_through=None,
        )
        status_fn = AsyncMock(return_value=raw)
        inspector = ProcessingStatusInspector(topic_memory_status=status_fn)

        result = _run(inspector._topic_memory_snapshot(Mock(), "scope-1", datetime(2026, 1, 1, tzinfo=UTC)))

        assert result.state is ProcessingStatusState.BLOCKED
        assert result.block is not None
        assert result.block.block_id == "b1"
        assert result.block.stage == "topic_memory"
        assert result.block.error_code == "window_attempt_limit"
        assert result.block.diagnostics is not None

    def test_dream_idle_when_no_matching_runs(self) -> None:
        dream = MagicMock()
        dream.list = AsyncMock(return_value=Mock(runs=()))
        inspector = ProcessingStatusInspector(dream=dream)

        result = _run(inspector.status(Mock(), "scope-1", "skill"))

        assert result.state is ProcessingStatusState.IDLE
        assert result.dream_operations == ()

    def test_dream_blocked_when_latest_run_failed(self) -> None:
        failed_run = Mock(
            run_id="dr_1",
            operation="derive_skill",
            status="failed",
            outcome=None,
            error="budget_exceeded",
        )
        dream = MagicMock()
        dream.list = AsyncMock(return_value=Mock(runs=(failed_run,)))
        inspector = ProcessingStatusInspector(dream=dream)

        result = _run(inspector.status(Mock(), "scope-1", "skill"))

        assert result.state is ProcessingStatusState.BLOCKED
        assert len(result.dream_operations) == 1
        assert result.dream_operations[0].run_id == "dr_1"

    def test_dream_pending_when_latest_run_queued(self) -> None:
        queued_run = Mock(run_id="dr_2", operation="refresh_handoff", status="queued", outcome=None, error=None)
        dream = MagicMock()
        dream.list = AsyncMock(return_value=Mock(runs=(queued_run,)))
        inspector = ProcessingStatusInspector(dream=dream)

        result = _run(inspector.status(Mock(), "scope-1", "handoff"))

        assert result.state is ProcessingStatusState.PENDING

    def test_dream_pending_when_latest_run_running(self) -> None:
        running_run = Mock(run_id="dr_3", operation="revise_prompt", status="running", outcome=None, error=None)
        dream = MagicMock()
        dream.list = AsyncMock(return_value=Mock(runs=(running_run,)))
        inspector = ProcessingStatusInspector(dream=dream)

        result = _run(inspector.status(Mock(), "scope-1", "prompt"))

        assert result.state is ProcessingStatusState.PENDING

    def test_dream_idle_when_latest_succeeded(self) -> None:
        succeeded_run = Mock(
            run_id="dr_4",
            operation="derive_skill",
            status="succeeded",
            outcome="proposed",
            error=None,
        )
        dream = MagicMock()
        dream.list = AsyncMock(return_value=Mock(runs=(succeeded_run,)))
        inspector = ProcessingStatusInspector(dream=dream)

        result = _run(inspector.status(Mock(), "scope-1", "skill"))

        assert result.state is ProcessingStatusState.IDLE

    def test_dream_idle_when_query_fails(self) -> None:
        dream = MagicMock()
        dream.list = AsyncMock(side_effect=RuntimeError("db unavailable"))
        inspector = ProcessingStatusInspector(dream=dream)

        result = _run(inspector.status(Mock(), "scope-1", "skill"))

        assert result.state is ProcessingStatusState.UNKNOWN
        assert result.details["reason"] == "dream_query_failed"

    def test_status_routes_to_correct_family(self) -> None:
        intent = _make_intent(dirty_generation=0, clean_generation=0)
        intents = MagicMock()
        intents.load = AsyncMock(return_value=intent)
        inspector = ProcessingStatusInspector(intents=intents)

        result = _run(inspector.status(Mock(), "scope-1", "memory"))

        assert result.family == "memory"
        assert result.state is ProcessingStatusState.IDLE

    def test_unknown_family_returns_unsupported(self) -> None:
        inspector = ProcessingStatusInspector()

        result = _run(inspector.status(Mock(), "scope-1", "nonexistent-family"))

        assert result.state is ProcessingStatusState.UNKNOWN
        assert result.details["reason"] == "family_not_supported"

    def test_dream_filters_runs_by_family_operations(self) -> None:
        """Runs from other families must not appear in this family's status."""
        skill_run = Mock(
            run_id="dr_skill",
            operation="derive_skill",
            status="succeeded",
            outcome="proposed",
            error=None,
        )
        handoff_run = Mock(
            run_id="dr_handoff",
            operation="refresh_handoff",
            status="failed",
            outcome=None,
            error="err",
        )
        dream = MagicMock()
        dream.list = AsyncMock(return_value=Mock(runs=(skill_run, handoff_run)))
        inspector = ProcessingStatusInspector(dream=dream)

        result = _run(inspector.status(Mock(), "scope-1", "skill"))

        assert result.state is ProcessingStatusState.IDLE
        assert len(result.dream_operations) == 1
        assert result.dream_operations[0].operation == "derive_skill"


def test_utc_now_is_timezone_aware() -> None:
    now = _utc_now()
    assert now.tzinfo is not None
    assert now.tzinfo.utcoffset(now) is not None


def test_observation_origin_values() -> None:
    assert ObservationOrigin.DURABLE == "durable"
    assert ObservationOrigin.LOCAL == "local"
    assert ObservationOrigin.EXTERNAL == "external"


def test_processing_status_state_values() -> None:
    assert ProcessingStatusState.IDLE == "idle"
    assert ProcessingStatusState.PENDING == "pending"
    assert ProcessingStatusState.BLOCKED == "blocked"
    assert ProcessingStatusState.UNKNOWN == "unknown"
