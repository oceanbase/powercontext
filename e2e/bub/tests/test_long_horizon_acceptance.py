from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from powercontext_e2e.catalog import load_tasks
from powercontext_e2e.evaluation import MemoryEvaluator
from powercontext_e2e.models import (
    CaptureRecord,
    HarborTrialObservation,
    MemoryEntrySnapshot,
    MemorySnapshot,
    NativeArtifact,
    PreparedContextSnapshot,
    RecallProbeObservation,
    ResolvedInstruction,
    RunEnvironment,
    SourceReferenceSnapshot,
    TaskObservation,
)


def test_memory_acceptance_does_not_require_the_harbor_task_to_pass() -> None:
    repository = Path(__file__).resolve().parents[3]
    task = next(
        task for task in load_tasks(repository / "e2e" / "bub" / "tasks") if task.id == "terminal-bench-db-wal-recovery"
    )
    recorded_at = datetime(2026, 8, 13, tzinfo=UTC)
    captured_source = "bub-event:captured"
    observation = TaskObservation(
        run_id="behavior-test",
        environment=RunEnvironment(
            commit="abcdef0",
            database="sqlite",
            adapter_version="test-adapter",
            adapter_protocol_version="test-protocol",
            agent_model="test:model",
            started_at=recorded_at,
            finished_at=recorded_at,
        ),
        task=task,
        status="completed",
        harbor=HarborTrialObservation(
            job_id="job-id",
            trial_name="trial-name",
            task_checksum=task.dataset.checksum,
            rewards={"reward": 1 / 3},
        ),
        capture_records=(
            CaptureRecord(
                schema="powercontext.bub-capture-event/v1",
                recorded_at=recorded_at,
                event="user_prompt",
                status="captured",
                sequence=1,
                source_id=captured_source,
                source_position=1,
            ),
            CaptureRecord(
                schema="powercontext.bub-capture-event/v1",
                recorded_at=recorded_at,
                event="checkpoint",
                status="advanced",
                target_position=1,
                current_cursor=1,
                memory_created=True,
            ),
            CaptureRecord(
                schema="powercontext.bub-capture-event/v1",
                recorded_at=recorded_at,
                event="context",
                status="ready",
                content_bytes=128,
                captured_events=1,
                flushed_position=1,
            ),
        ),
        native_artifacts=tuple(
            NativeArtifact(name=name, sha256="a" * 64, bytes=1)
            for name in ("acp-summary.json", "acp-events.jsonl", "trajectory.json")
        ),
        resolved_instructions=(
            ResolvedInstruction(
                artifact="acp-summary.json",
                content="Recover the database and write /app/recovered.json.",
                sha256="b" * 64,
            ),
        ),
        memory_before=MemorySnapshot(),
        memory_after=MemorySnapshot(
            entries=(
                MemoryEntrySnapshot(
                    entry_id="entry-1",
                    entry_version_id="entry-version-1",
                    version=1,
                    kind="task-finding",
                    text="The WAL investigation produced a grounded finding.",
                    state="active",
                    source_refs=(SourceReferenceSnapshot(name="content", source_id=captured_source),),
                ),
            )
        ),
        probes=tuple(
            RecallProbeObservation(
                id=probe.id,
                query=probe.query,
                prepared_context=PreparedContextSnapshot(status="ready", content="Grounded task evidence."),
            )
            for probe in task.evaluation.probes
        ),
    )

    report = MemoryEvaluator.evaluate(observation, experiment="behavior-test")

    assert report.accepted
    assert report.cases[0].scores["harbor_reward_reward"].value == 1 / 3
    assert report.cases[0].labels["task_outcome"].value == "not_passed"
