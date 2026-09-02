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
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from powercontext_e2e.artifacts import write_artifacts
from powercontext_e2e.catalog import E2ETask, MemoryEvaluationSpec, load_tasks
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
from powercontext_e2e.rescore import rescore_replay
from powercontext_e2e.runner import _prepared_probes
from powercontext_e2e.settings import HarnessSettings


def test_memory_acceptance_does_not_require_the_harbor_task_to_pass() -> None:
    task = _terminal_bench_task()
    observation = _observation(task, prepared_context="Grounded task evidence.")

    report = MemoryEvaluator.evaluate(observation, experiment="behavior-test")

    assert report.accepted
    assert report.cases[0].scores["harbor_reward_reward"].value == 1 / 3
    assert report.cases[0].labels["task_outcome"].value == "not_passed"


def test_memory_acceptance_rejects_forbidden_recall_context() -> None:
    task = _task_with_recall_contract(
        expected_context=("grounded task evidence",),
        forbidden_context=("OBSOLETE WAL GUIDANCE",),
        probe_coverage=0,
    )
    clean_observation = _observation(task, prepared_context="Grounded task evidence is current.")
    clean_report = MemoryEvaluator.evaluate(clean_observation, experiment="negative-context-control")

    assert clean_report.accepted

    observation = _observation(
        task,
        prepared_context="Grounded task evidence still includes obsolete wal guidance.",
    )

    report = MemoryEvaluator.evaluate(observation, experiment="negative-context-test")

    assert not report.accepted
    case = report.cases[0]
    assert case.metrics["recall_probes_supported"] == 0
    assert case.assertions["recall_probes_supported"].value
    assert case.assertions["forbidden_context_absent"].reason == (
        "Forbidden context matched for probes: ['investigation']."
    )


def test_memory_acceptance_normalizes_forbidden_recall_context() -> None:
    task = _task_with_recall_contract(
        expected_context=("grounded task evidence",),
        forbidden_context=("CAFÉ",),
        probe_coverage=0,
    )
    observation = _observation(task, prepared_context="Grounded task evidence includes cafe\u0301 notes.")

    report = MemoryEvaluator.evaluate(observation, experiment="unicode-context-test")

    assert not report.accepted
    assert not report.cases[0].assertions["forbidden_context_absent"].value


def test_memory_acceptance_normalizes_required_recall_context() -> None:
    task = _task_with_recall_contract(
        expected_context=("CAFÉ",),
        forbidden_context=(),
    )
    observation = _observation(task, prepared_context="Relevant cafe\u0301 notes.")

    report = MemoryEvaluator.evaluate(observation, experiment="unicode-required-context-test")

    assert report.accepted
    assert report.cases[0].metrics["recall_probes_supported"] == 1


def test_memory_acceptance_supports_empty_purely_negative_probe() -> None:
    task = _task_with_recall_contract(forbidden_context=("obsolete guidance",))
    observation = _observation(
        task,
        prepared_context="",
        prepared_context_status="empty",
    )

    report = MemoryEvaluator.evaluate(observation, experiment="negative-abstention-test")

    assert report.accepted
    case = report.cases[0]
    assert case.assertions["forbidden_context_absent"].value
    assert case.scores["probe_coverage"].value == 1
    assert case.metrics["recall_probes_supported"] == 0


def test_memory_acceptance_excludes_purely_negative_probes_from_coverage() -> None:
    task = _task_with_recall_contract(
        expected_context=("grounded task evidence",),
        forbidden_context=(),
    )
    evaluation = task.evaluation
    assert isinstance(evaluation, MemoryEvaluationSpec)
    positive_probe = evaluation.probes[0]
    negative_probe = positive_probe.model_copy(
        update={
            "id": "no-obsolete-guidance",
            "expected_context": (),
            "forbidden_context": ("obsolete guidance",),
        }
    )
    task = task.model_copy(
        update={"evaluation": evaluation.model_copy(update={"probes": (positive_probe, negative_probe)})}
    )
    observation = _observation(
        task,
        prepared_context="",
        prepared_context_by_probe={
            positive_probe.id: PreparedContextSnapshot(status="ready", content="Grounded task evidence."),
            negative_probe.id: PreparedContextSnapshot(status="empty"),
        },
    )

    report = MemoryEvaluator.evaluate(observation, experiment="mixed-probe-coverage-test")

    assert report.accepted
    case = report.cases[0]
    assert case.scores["probe_coverage"].value == 1
    assert case.metrics["recall_probes_supported"] == 1


def test_prepared_probes_discard_forbidden_context_after_recording_verdict() -> None:
    task = _task_with_recall_contract(forbidden_context=("SECRET",))
    probes = _prepared_probe_observations(task, "prefix-SECRET-suffix")

    assert probes[0].forbidden_context_matched is True
    assert probes[0].prepared_context.content == ""


def test_redacted_replay_preserves_forbidden_context_verdict(monkeypatch, tmp_path: Path) -> None:
    sensitive_value = "prefix-CAFÉ-suffix"
    monkeypatch.setenv("BUB_API_KEY", sensitive_value)
    settings = HarnessSettings()
    task = _task_with_recall_contract(forbidden_context=("CAFÉ",))
    probes = _prepared_probe_observations(task, "prefix-cafe\u0301-suffix")
    observation = _observation(task, prepared_context="unused").model_copy(
        update={"probes": probes},
    )
    live_report = MemoryEvaluator.evaluate(observation, experiment="live")
    live_dir = tmp_path / "live"

    write_artifacts(observation, live_report, live_dir, settings=settings)
    replay = json.loads((live_dir / "replay.json").read_text(encoding="utf-8"))
    offline_accepted = rescore_replay(live_dir / "replay.json", tmp_path / "offline", settings)

    assert not live_report.accepted
    assert sensitive_value not in json.dumps(replay)
    assert replay["probes"][0]["forbidden_context_matched"] is True
    assert replay["probes"][0]["prepared_context"]["content"] == ""
    assert not offline_accepted


def test_redacted_replay_trusts_recorded_clean_verdict(monkeypatch, tmp_path: Path) -> None:
    sensitive_value = "provider-runtime-sensitive-value"
    monkeypatch.setenv("BUB_API_KEY", sensitive_value)
    settings = HarnessSettings()
    task = _task_with_recall_contract(forbidden_context=("[REDACTED]",))
    probes = _prepared_probe_observations(task, sensitive_value)
    observation = _observation(task, prepared_context="unused").model_copy(
        update={"probes": probes},
    )
    live_report = MemoryEvaluator.evaluate(observation, experiment="live-clean")
    live_dir = tmp_path / "live-clean"

    write_artifacts(observation, live_report, live_dir, settings=settings)
    replay = json.loads((live_dir / "replay.json").read_text(encoding="utf-8"))
    offline_accepted = rescore_replay(live_dir / "replay.json", tmp_path / "offline-clean", settings)

    assert live_report.accepted
    assert replay["probes"][0]["prepared_context"]["content"] == "[REDACTED]"
    assert replay["probes"][0]["forbidden_context_matched"] is False
    assert offline_accepted


def _terminal_bench_task() -> E2ETask:
    repository = Path(__file__).resolve().parents[3]
    return next(
        task for task in load_tasks(repository / "e2e" / "bub" / "tasks") if task.id == "terminal-bench-db-wal-recovery"
    )


def _task_with_recall_contract(
    *,
    expected_context: tuple[str, ...] = (),
    forbidden_context: tuple[str, ...],
    probe_coverage: float = 1,
) -> E2ETask:
    task = _terminal_bench_task()
    evaluation = task.evaluation
    assert isinstance(evaluation, MemoryEvaluationSpec)
    probe = evaluation.probes[0].model_copy(
        update={
            "expected_context": expected_context,
            "forbidden_context": forbidden_context,
        }
    )
    return task.model_copy(
        update={
            "evaluation": evaluation.model_copy(
                update={
                    "probes": (probe,),
                    "thresholds": evaluation.thresholds.model_copy(update={"probe_coverage": probe_coverage}),
                }
            )
        }
    )


def _prepared_probe_observations(task: E2ETask, content: str) -> tuple[RecallProbeObservation, ...]:
    evaluation = task.evaluation
    assert isinstance(evaluation, MemoryEvaluationSpec)
    client = SimpleNamespace(
        prepare_context=AsyncMock(
            return_value=SimpleNamespace(
                status=SimpleNamespace(value="ready"),
                content=content,
            )
        )
    )
    return asyncio.run(_prepared_probes(client, evaluation, "scope-id"))


def _observation(
    task: E2ETask,
    *,
    prepared_context: str,
    prepared_context_status: str = "ready",
    prepared_context_by_probe: dict[str, PreparedContextSnapshot] | None = None,
    forbidden_context_matched: bool | None = None,
) -> TaskObservation:
    recorded_at = datetime(2026, 8, 13, tzinfo=UTC)
    captured_source = "bub-event:captured"
    return TaskObservation(
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
                prepared_context=(
                    prepared_context_by_probe[probe.id]
                    if prepared_context_by_probe is not None
                    else PreparedContextSnapshot(status=prepared_context_status, content=prepared_context)
                ),
                forbidden_context_matched=forbidden_context_matched,
            )
            for probe in task.evaluation.probes
        ),
    )
