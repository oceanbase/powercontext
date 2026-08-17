from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from powercontext_e2e.models import CaptureRecord
from powercontext_e2e.scenario_support import restore_workspace, snapshot_workspace
from powercontext_e2e.usage import summarize_bub_metrics


def test_scenario_metrics_aggregate_bub_usage_without_double_counting() -> None:
    recorded_at = datetime(2026, 8, 17, tzinfo=UTC)
    records = (
        CaptureRecord(
            schema="powercontext.bub-capture-event/v1",
            recorded_at=recorded_at,
            event="llm_result",
            status="captured",
            usage={
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
                "input_tokens_details": {"cached_tokens": 40},
                "output_tokens_details": {"reasoning_tokens": 5},
            },
        ),
        CaptureRecord(
            schema="powercontext.bub-capture-event/v1",
            recorded_at=recorded_at,
            event="llm_result",
            status="captured",
            usage={"prompt_tokens": 80, "completion_tokens": 10},
        ),
        CaptureRecord(
            schema="powercontext.bub-capture-event/v1",
            recorded_at=recorded_at,
            event="tool_result",
            status="captured",
        ),
    )

    metrics = summarize_bub_metrics(records)

    assert metrics.input_tokens == 180
    assert metrics.output_tokens == 30
    assert metrics.total_tokens == 210
    assert metrics.cached_input_tokens == 40
    assert metrics.reasoning_tokens == 5
    assert metrics.llm_calls == 2
    assert metrics.llm_calls_with_usage == 2
    assert metrics.tool_calls == 1


def test_container_handoff_restores_workspace_state_including_deletions(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "kept.txt").write_text("before", encoding="utf-8")
    archive = tmp_path / "handoff" / "workspace.tar"
    snapshot_workspace(workspace, archive)

    (workspace / "kept.txt").write_text("after", encoding="utf-8")
    (workspace / "extra.txt").write_text("extra", encoding="utf-8")
    restore_workspace(workspace, archive)

    assert (workspace / "kept.txt").read_text(encoding="utf-8") == "before"
    assert not (workspace / "extra.txt").exists()
