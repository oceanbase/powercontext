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

"""OFF/ON continuation workloads: grading, treatment checks, outcome rules, and the paired summary."""

from __future__ import annotations

import asyncio
import re
import runpy
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from harbor.models.agent.context import AgentContext
from harbor.models.task.task import Task as HarborTask
from harbor.models.trial.result import StepResult, TimingInfo
from harbor.models.verifier.result import VerifierResult
from powercontext.client import UnauthorizedResponseError, UnavailableResponseError

from powercontext_e2e import paired as paired_module
from powercontext_e2e import sessions as sessions_module
from powercontext_e2e.catalog import TaskOutcomeComparisonSpec, load_tasks
from powercontext_e2e.models import (
    HarborTrialObservation,
    MetricSummary,
    PairedAgent,
    PairedArmObservation,
    RunEnvironment,
    SessionSnapshot,
    StepObservation,
)
from powercontext_e2e.paired import (
    ScoredSession,
    UnauthenticatedServerError,
    arm_outcome,
    checksum_failure,
    classify_outcome,
    recall_session_index,
    require_authenticated_server,
    run_paired,
    scored_session,
    single_session_step,
    step_observations,
    summarize,
    treatment_failures,
)
from powercontext_e2e.report import render_paired_report
from powercontext_e2e.runner import run_tasks
from powercontext_e2e.sessions import SessionRecorder, settle_session
from powercontext_e2e.settings import HarnessSettings

_REPOSITORY = Path(__file__).resolve().parents[3]
_PAIRED_TASKS = load_tasks(_REPOSITORY / "e2e" / "bub" / "paired-tasks")
_HARBOR_TASKS = _REPOSITORY / "e2e" / "bub" / "harbor-tasks"
_SETTINGS = HarnessSettings(repository=_REPOSITORY)


def _grade(answer_path: Path, reward_path: Path, task: str = "project-decision-continuation") -> None:
    # run_path does not write bytecode, which would change the Harbor task checksum.
    grader = runpy.run_path(str(_HARBOR_TASKS / task / "steps" / "recall" / "tests" / "grade.py"))
    grader["main"](answer_path, reward_path)


@pytest.mark.parametrize(
    ("task", "answer", "reward"),
    [
        ("project-decision-continuation", '{"database": "OceanBase", "shard_count": 12}', 1),
        ("project-decision-continuation", '{"database": " oceanbase ", "shard_count": "12"}', 1),
        # Contradictory answers name the right fact somewhere but assert another value.
        ("project-decision-continuation", '{"database": "OceanBase", "shard_count": 24}', 0),
        ("project-decision-continuation", '{"database": "PostgreSQL", "shard_count": 12}', 0),
        ("project-decision-continuation", "We chose OceanBase with 24 shards, not 12.", 0),
        ("project-decision-continuation", "We chose PostgreSQL rather than OceanBase, with 12 shards.", 0),
        # Uncertain answers do not assert the decision.
        ("project-decision-continuation", '{"database": null, "shard_count": null}', 0),
        ("project-decision-continuation", '{"database": "OceanBase?", "shard_count": 12}', 0),
        ("project-decision-continuation", '{"database": "maybe OceanBase", "shard_count": 12}', 0),
        ("project-decision-continuation", '{"database": "OceanBase", "shard_count": "about 12"}', 0),
        # A fullwidth O is a different name, not a case variant; fullwidth digits are not digits either.
        ("project-decision-continuation", '{"database": "\uff2fceanBase", "shard_count": 12}', 0),
        ("project-decision-continuation", '{"database": "OceanBase", "shard_count": "\uff11\uff12"}', 0),
        ("project-decision-continuation", '["OceanBase", 12]', 0),
        # A repeated key or an extra field could hide a contradiction from the checked values.
        ("project-decision-continuation", '{"database": "PostgreSQL", "database": "OceanBase", "shard_count": 12}', 0),
        (
            "project-decision-continuation",
            '{"database": "OceanBase", "shard_count": 12, "note": "or PostgreSQL with 24"}',
            0,
        ),
        # Header names are case-insensitive in ASCII; paths are exact. A look-alike character is a different name.
        ("api-contract-continuation", '{"path": "/v3/ledger/settle", "header": "X-Ledger-Idempotency-Key"}', 1),
        ("api-contract-continuation", '{"path": "/v3/ledger/settle", "header": " x-ledger-idempotency-key "}', 1),
        ("api-contract-continuation", '{"path": "/v3/ledger/settle", "header": "X-LEDGER-IDEMPOTENCY-KEY"}', 1),
        ("api-contract-continuation", '{"path": " /v3/ledger/settle ", "header": "X-Ledger-Idempotency-Key"}', 1),
        # Superscript three in the path; fullwidth X, then the Kelvin sign (which lowercases to ASCII k) in the header.
        ("api-contract-continuation", '{"path": "/v\u00b3/ledger/settle", "header": "X-Ledger-Idempotency-Key"}', 0),
        ("api-contract-continuation", '{"path": "/v3/ledger/settle", "header": "\uff38-Ledger-Idempotency-Key"}', 0),
        ("api-contract-continuation", '{"path": "/v3/ledger/settle", "header": "X-Ledger-Idempotency-\u212aey"}', 0),
        ("api-contract-continuation", '{"path": "/V3/Ledger/Settle", "header": "X-Ledger-Idempotency-Key"}', 0),
        ("api-contract-continuation", '{"path": "/v3/ledger/settle/", "header": "X-Ledger-Idempotency-Key"}', 0),
        ("api-contract-continuation", '{"path": "/v3/ledger/settle", "header": "Idempotency-Key"}', 0),
        ("api-contract-continuation", '{"path": "/v3/ledger/settle", "header": null}', 0),
        (
            "api-contract-continuation",
            '{"path": "/v3/ledger/settle", "header": "X-Ledger-Idempotency-Key", "method": "POST"}',
            0,
        ),
        # The current value and the superseded one must both be right, in their own keys.
        ("revised-decision-continuation", '{"ttl_seconds": 90, "previous_ttl_seconds": 30}', 1),
        ("revised-decision-continuation", '{"ttl_seconds": "90", "previous_ttl_seconds": "30"}', 1),
        ("revised-decision-continuation", '{"ttl_seconds": "\uff19\uff10", "previous_ttl_seconds": 30}', 0),
        ("revised-decision-continuation", '{"ttl_seconds": 30, "previous_ttl_seconds": 90}', 0),
        ("revised-decision-continuation", '{"ttl_seconds": 30, "previous_ttl_seconds": 30}', 0),
        ("revised-decision-continuation", '{"ttl_seconds": 90, "previous_ttl_seconds": null}', 0),
        ("revised-decision-continuation", '{"ttl_seconds": 90, "previous_ttl_seconds": 30, "note": "or 60"}', 0),
        ("revised-decision-continuation", "The TTL is 90 seconds, previously 30.", 0),
    ],
)
def test_recall_graders_check_the_asserted_values(tmp_path: Path, task: str, answer: str, reward: int) -> None:
    answer_path = tmp_path / "answer.json"
    answer_path.write_text(answer, encoding="utf-8")
    reward_path = tmp_path / "reward.txt"

    _grade(answer_path, reward_path, task)

    assert reward_path.read_text(encoding="utf-8") == f"{reward}\n"


@pytest.mark.parametrize("task", _PAIRED_TASKS, ids=lambda task: task.id)
def test_recall_grader_scores_a_missing_answer_as_zero(tmp_path: Path, task) -> None:
    reward_path = tmp_path / "reward.txt"

    _grade(tmp_path / "answer.json", reward_path, task.dataset.task_id)

    assert reward_path.read_text(encoding="utf-8") == "0\n"


@pytest.mark.parametrize("task", _PAIRED_TASKS, ids=lambda task: task.id)
def test_continuation_tasks_hide_the_answer_until_the_recall_session(task) -> None:
    # Harbor uploads shared tests before every step and leaves them in the container, so an answer key there would
    # be readable in the earlier session.
    assert recall_session_index(task, _SETTINGS) >= 1
    assert not (_HARBOR_TASKS / task.dataset.task_id / "tests").exists()


_LICENSE_HEADER = (
    (_HARBOR_TASKS / "project-decision-continuation" / "steps" / "recall" / "tests" / "test.sh")
    .read_text(encoding="utf-8")[len("#!/bin/sh\n") :]
    .split("\n\n", 1)[0]
)


@pytest.mark.parametrize("task", _PAIRED_TASKS, ids=lambda task: task.id)
def test_task_files_do_not_hold_the_answer(task) -> None:
    # The fact belongs in the capture instruction alone, and the answer key in the recall tests. Both arms can read
    # every other file the task puts in the container, so none may hold an expected value. The repository's license
    # header is on every file and is skipped; the "OceanBase" it leaves in the capture tests is out of the recall
    # session's reach because the harness empties /tests before each session.
    task_dir = _HARBOR_TASKS / task.dataset.task_id
    recall_tests = task_dir / "steps" / "recall" / "tests"
    grader = runpy.run_path(str(recall_tests / "grade.py"))
    expected = {name: value for name, value in grader.items() if name.startswith("EXPECTED_")}
    assert expected
    capture_instruction = task_dir / "steps" / "capture" / "instruction.md"
    visible = [
        path
        for path in task_dir.rglob("*")
        if path.is_file() and path != capture_instruction and not path.is_relative_to(recall_tests)
    ]
    assert visible
    for path in visible:
        content = path.read_text(encoding="utf-8").replace(_LICENSE_HEADER, "").casefold()
        for name, value in expected.items():
            if isinstance(value, int):
                found = re.search(rf"(?<!\d)(?<!\d\.){value}(?!\d)(?!\.\d)", content) is not None
            else:
                found = str(value).casefold() in content
            assert not found, f"{path} holds {name}"


def test_recall_step_must_be_the_final_session() -> None:
    task = _PAIRED_TASKS[0]
    task = task.model_copy(update={"evaluation": task.evaluation.model_copy(update={"recall_step": "capture"})})

    with pytest.raises(ValueError, match="must end with its recall step"):
        recall_session_index(task, _SETTINGS)


@pytest.mark.parametrize("manifest", ["project-decision-continuation.yaml", "swebench-pro"])
def test_acceptance_rejects_paired_workloads(tmp_path: Path, manifest: str) -> None:
    tasks = load_tasks(_REPOSITORY / "e2e" / "bub" / "paired-tasks" / manifest)

    with pytest.raises(ValueError, match="paired command"):
        asyncio.run(run_tasks(tasks[:1], output_dir=tmp_path / "out", settings=_SETTINGS))


def _snapshot(session: int, *, sources: int = 1, memory: int = 1, asked: int = 0, ready: int = 0) -> SessionSnapshot:
    return SessionSnapshot(
        session=session,
        flush_rounds=1,
        sources=sources,
        memory_pending=0,
        memory_entries=memory,
        preparations=asked,
        ready_preparations=ready,
    )


def test_treatment_passes_when_recall_received_context_from_captured_memory() -> None:
    sessions = (_snapshot(0, asked=1, ready=1), _snapshot(1, asked=2, ready=2))

    assert treatment_failures(sessions, recall_session=1) == ()


def test_treatment_passes_when_powercontext_keeps_or_returns_nothing() -> None:
    # The integration captured the earlier session and asked during recall; an empty answer is a scored ON failure,
    # not an excluded run.
    sessions = (_snapshot(0, memory=0, asked=4), _snapshot(1, memory=0, asked=8))

    assert treatment_failures(sessions, recall_session=1) == ()


@pytest.mark.parametrize(
    ("sessions", "failure"),
    [
        ((_snapshot(0, sources=0, memory=0), _snapshot(1, asked=1)), "No Sources were captured"),
        # Requests made during the capture session do not show that the recall session asked.
        ((_snapshot(0, asked=4, ready=2), _snapshot(1, asked=4, ready=2)), "not asked for context"),
        ((_snapshot(0),), "not observed after every session"),
    ],
)
def test_treatment_fails_when_the_integration_did_not_capture_or_ask(sessions, failure: str) -> None:
    assert any(failure in reason for reason in treatment_failures(sessions, recall_session=1))


def test_the_recall_step_reward_decides_the_arm_whatever_harbor_averaged() -> None:
    # Harbor averages step rewards unless a task opts into its final-step strategy; an unfinished capture-session
    # chore must not turn a correct recall into a failure.
    steps = (
        StepResult(step_name="capture", verifier_result=VerifierResult(rewards={"reward": 0})),
        StepResult(step_name="recall", verifier_result=VerifierResult(rewards={"reward": 1})),
    )
    averaged = HarborTrialObservation(rewards={"reward": 0.5})

    outcome = arm_outcome(steps, averaged, scored_step="recall", harness_failed=False, treatment_failures=())

    assert outcome == "passed"


def test_continuation_tasks_cannot_gate_the_recall_step_behind_an_earlier_reward(tmp_path: Path) -> None:
    # Harbor skips the remaining steps when a step scores below its min_reward.
    task = next(task for task in _PAIRED_TASKS if task.id == "project-decision-continuation")
    task_dir = tmp_path / "e2e" / "bub" / "harbor-tasks" / task.dataset.task_id
    shutil.copytree(_HARBOR_TASKS / task.dataset.task_id, task_dir)
    config = task_dir / "task.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace('name = "capture"', 'name = "capture"\nmin_reward = 1.0'),
        encoding="utf-8",
    )
    task = task.model_copy(
        update={"dataset": task.dataset.model_copy(update={"checksum": HarborTask(task_dir).checksum})}
    )

    with pytest.raises(ValueError, match="min_reward"):
        recall_session_index(task, HarnessSettings(repository=tmp_path))


@pytest.mark.parametrize(
    ("kwargs", "outcome"),
    [
        ({"reward": 1.0}, "passed"),
        ({"reward": 0.0}, "failed"),
        ({"reward": None}, "failed"),
        ({"exception_types": ("AgentTimeoutError",)}, "timeout"),
        ({"exception_types": ("EnvironmentStartTimeoutError",)}, "error"),
        ({"harness_failed": True, "reward": 1.0}, "error"),
        ({"treatment_failures": ("no context",), "reward": 1.0}, "integration_failed"),
        # A timed-out ON run counts as a failed attempt, as it would with OFF, even when it also missed the treatment.
        ({"exception_types": ("AgentTimeoutError",), "treatment_failures": ("not observed",)}, "timeout"),
    ],
)
def test_outcome_classification(kwargs, outcome: str) -> None:
    arguments = {"harness_failed": False, "exception_types": (), "treatment_failures": (), "reward": None} | kwargs

    assert classify_outcome(**arguments) == outcome


def _observation(
    trial: int,
    arm: str,
    outcome: str,
    task_id: str = "task",
    *,
    steps: tuple[StepObservation, ...] = (),
    sessions: tuple[SessionSnapshot, ...] = (),
) -> PairedArmObservation:
    now = datetime.now(UTC)
    return PairedArmObservation(
        run_id=f"{task_id}-{trial}-{arm}",
        task_id=task_id,
        trial=trial,
        arm=arm,
        position=1,
        environment=RunEnvironment(
            commit="c",
            database="sqlite",
            adapter_version="a",
            adapter_protocol_version="p",
            started_at=now,
            finished_at=now,
        ),
        harbor=HarborTrialObservation(),
        outcome=outcome,
        steps=steps,
        sessions=sessions,
    )


_AGENT = PairedAgent(host="bub", version="0", model="provider:model")


def test_summary_pairs_only_trials_where_both_arms_were_scored() -> None:
    report = summarize(
        (
            _observation(1, "off", "failed"),
            _observation(1, "on", "passed"),
            _observation(2, "off", "timeout"),
            _observation(2, "on", "integration_failed"),
            _observation(3, "off", "passed"),
            _observation(3, "on", "passed"),
        ),
        trials=3,
        agent=_AGENT,
    )

    (task,) = report.tasks
    assert (task.off.passed, task.off.scored, task.off.timeouts) == (1, 3, 1)
    assert (task.on.passed, task.on.scored, task.on.integration_failures) == (2, 2, 1)
    assert task.pairs == 2
    assert task.mean_delta == 0.5
    assert (report.total.pairs, report.total.mean_delta) == (2, 0.5)


def test_summary_reports_no_difference_without_a_scored_pair() -> None:
    report = summarize((_observation(1, "off", "error"), _observation(1, "on", "passed")), trials=1, agent=_AGENT)

    assert report.total.pairs == 0
    assert report.total.mean_delta is None
    assert report.total.delta_interval is None
    assert report.total.off.errors == 1
    assert report.total.off.success_rate is None
    assert report.total.off.success_rate_interval is None


def test_summary_reports_intervals_and_which_arm_won_each_pair() -> None:
    report = summarize(
        (
            _observation(1, "off", "failed"),
            _observation(1, "on", "passed"),
            _observation(2, "off", "passed"),
            _observation(2, "on", "passed"),
            _observation(3, "off", "passed"),
            _observation(3, "on", "failed"),
        ),
        trials=3,
        agent=_AGENT,
    )

    total = report.total
    assert (total.on_better, total.off_better, total.tied) == (1, 1, 1)
    assert total.mean_delta == 0
    assert total.delta_interval is not None
    # Resampling three pairs with scores -1, 0, and +1 reaches both extremes.
    assert (total.delta_interval.low, total.delta_interval.high) == (-1, 1)
    assert total.off.success_rate == pytest.approx(2 / 3)
    assert total.off.success_rate_interval is not None
    assert total.off.success_rate_interval.low < 2 / 3 < total.off.success_rate_interval.high


def _step(name: str, *, seconds: float | None, tokens: int | None = None) -> StepObservation:
    return StepObservation(
        name=name,
        seconds=seconds,
        input_tokens=tokens,
        cache_tokens=None if tokens is None else tokens // 2,
        output_tokens=None if tokens is None else 10,
        cost_usd=None if tokens is None else tokens / 1_000_000,
    )


def test_summary_step_metrics_cover_the_scored_runs_and_the_metrics_a_host_reports() -> None:
    report = summarize(
        (
            _observation(
                1, "off", "failed", steps=(_step("capture", seconds=4), _step("recall", seconds=2, tokens=6000))
            ),
            _observation(
                1, "on", "passed", steps=(_step("capture", seconds=6), _step("recall", seconds=5, tokens=30000))
            ),
            # A timed-out run is scored, so its time counts; a host that reports no usage leaves those metrics out.
            _observation(2, "off", "timeout", steps=(_step("capture", seconds=600), _step("recall", seconds=8))),
            # An error is not a scored run, so none of its figures count.
            _observation(2, "on", "error", steps=(_step("capture", seconds=1, tokens=1), _step("recall", seconds=1))),
        ),
        trials=2,
        agent=_AGENT,
    )

    off, on = report.total.off, report.total.on
    assert list(off.steps) == ["capture", "recall"]
    assert (off.steps["capture"].runs, off.steps["capture"].seconds.runs) == (2, 2)
    assert (off.steps["capture"].seconds.mean, off.steps["capture"].seconds.max) == (302, 600)
    assert off.steps["capture"].input_tokens is None
    assert (off.steps["recall"].input_tokens.runs, off.steps["recall"].input_tokens.mean) == (1, 6000)
    assert on.steps["recall"].input_tokens.mean == 30000
    assert on.steps["capture"].input_tokens is None


def test_summary_server_usage_comes_from_each_scored_on_runs_final_snapshot() -> None:
    def usage(session: int, *, generation: int, recalled: int) -> SessionSnapshot:
        return _snapshot(session, asked=1).model_copy(
            update={
                "generation_requests": generation,
                "generation_input_tokens": generation * 1000,
                "recalled_tokens": recalled,
            }
        )

    report = summarize(
        (
            _observation(1, "off", "failed"),
            _observation(
                1, "on", "passed", sessions=(usage(0, generation=1, recalled=0), usage(1, generation=3, recalled=1000))
            ),
            _observation(2, "off", "failed"),
            _observation(
                2, "on", "failed", sessions=(usage(0, generation=1, recalled=0), usage(1, generation=5, recalled=2000))
            ),
            _observation(3, "off", "failed"),
            _observation(3, "on", "integration_failed", sessions=(usage(0, generation=99, recalled=99),)),
        ),
        trials=3,
        agent=_AGENT,
    )

    assert report.total.off.server is None
    server = report.total.on.server
    assert server is not None
    assert (server.runs, server.generation_requests) == (2, 4)
    assert server.generation_input_tokens == MetricSummary(runs=2, mean=4000, min=3000, max=5000)
    assert (server.embedding_requests, server.embedding_input_tokens, server.recalled_tokens) == (0, None, 1500)


def test_step_observations_take_time_and_usage_from_harbor() -> None:
    started = datetime(2026, 10, 4, 18, 20, 40, tzinfo=UTC)
    steps = (
        StepResult(
            step_name="capture",
            agent_execution=TimingInfo(started_at=started, finished_at=started + timedelta(seconds=7.5)),
            agent_result=AgentContext(n_input_tokens=4599, n_cache_tokens=3264, n_output_tokens=127, cost_usd=0.0016),
        ),
        StepResult(step_name="recall", agent_execution=TimingInfo(started_at=started), agent_result=AgentContext()),
    )

    capture, recall = step_observations(steps)

    assert capture == StepObservation(
        name="capture", seconds=7.5, input_tokens=4599, cache_tokens=3264, output_tokens=127, cost_usd=0.0016
    )
    assert recall == StepObservation(name="recall")


def test_a_single_step_trial_records_its_session_as_the_step_task() -> None:
    # Harbor keeps a single-step trial's agent figures on the trial, where a task-outcome workload's session is.
    started = datetime(2026, 10, 9, 10, 0, 0, tzinfo=UTC)
    timing = TimingInfo(started_at=started, finished_at=started + timedelta(seconds=27))
    context = AgentContext(n_input_tokens=30462, n_cache_tokens=0, n_output_tokens=900, cost_usd=0.01)
    single = SimpleNamespace(
        trial_results=[SimpleNamespace(step_results=None, agent_result=context, agent_execution=timing)]
    )
    multi = SimpleNamespace(
        trial_results=[
            SimpleNamespace(step_results=[StepResult(step_name="recall")], agent_result=None, agent_execution=None)
        ]
    )
    unstarted = SimpleNamespace(
        trial_results=[SimpleNamespace(step_results=None, agent_result=None, agent_execution=None)]
    )

    (task,) = single_session_step(single)

    assert step_observations((task,)) == (
        StepObservation(name="task", seconds=27, input_tokens=30462, cache_tokens=0, output_tokens=900, cost_usd=0.01),
    )
    assert single_session_step(multi) == ()
    assert single_session_step(unstarted) == ()
    assert single_session_step(SimpleNamespace(trial_results=[])) == ()


def test_paired_report_renders_intervals_a_step_table_and_server_usage() -> None:
    report = summarize(
        (
            _observation(1, "off", "failed", steps=(_step("recall", seconds=3.5, tokens=6600),)),
            _observation(
                1,
                "on",
                "passed",
                steps=(_step("recall", seconds=4.3), _step("flush", seconds=1)),
                sessions=(_snapshot(0, asked=1),),
            ),
            _observation(2, "off", "failed", steps=(_step("recall", seconds=3.5, tokens=6400),)),
            # A scored run without a Scope snapshot is left out of the Server mean, and the report says so.
            _observation(2, "on", "timeout", steps=(_step("recall", seconds=4.3),)),
        ),
        trials=2,
        agent=_AGENT,
    )

    rendered = render_paired_report(report)

    assert "- OFF: 0/2 passed, 0% [0%, 66%] (0 timed out)" in rendered
    assert "- ON: 1/2 passed, 50% [9%, 91%] (1 timed out)" in rendered
    assert "ON minus OFF: +0.50 [+0.00, +1.00]; ON better in 1, OFF better in 0, tied in 1" in rendered
    assert "| recall | OFF | 2 | 3.5 (3.5-3.5) | 6,500 | 3,250 | 10 | 0.0065 |" in rendered
    assert "| recall | ON | 2 | 4.3 (4.3-4.3) | n/a | n/a | n/a | n/a |" in rendered
    # A step only one arm ran gets that arm's row alone.
    assert "| flush | ON | 1 | 1.0 (1.0-1.0) | n/a | n/a | n/a | n/a |" in rendered
    assert "| flush | OFF" not in rendered
    assert (
        "Server usage, mean over 1 scored ON run(s): generation 0.0 request(s), input tokens n/a, output tokens n/a"
    ) in rendered


def test_paired_report_gives_the_runs_behind_a_mean_only_some_runs_reported() -> None:
    def snapshot(tokens: int | None) -> SessionSnapshot:
        return _snapshot(0, asked=1).model_copy(
            update={"generation_requests": 1, "generation_input_tokens": tokens, "generation_output_tokens": tokens}
        )

    report = summarize(
        (
            # Both OFF runs are scored, but the timed-out one's host recorded no usage.
            _observation(1, "off", "passed", steps=(_step("recall", seconds=2, tokens=1000),)),
            _observation(2, "off", "timeout", steps=(_step("recall", seconds=600),)),
            # The Server leaves a Scope's tokens unknown when a provider did not report them.
            _observation(1, "on", "passed", sessions=(snapshot(800),)),
            _observation(2, "on", "passed", sessions=(snapshot(None),)),
        ),
        trials=2,
        agent=_AGENT,
    )

    server = report.total.on.server
    assert server is not None
    assert server.generation_input_tokens == MetricSummary(runs=1, mean=800, min=800, max=800)
    rendered = render_paired_report(report)
    assert (
        "| recall | OFF | 2 | 301.0 (2.0-600.0) | 1,000 (1 of 2 runs) | 500 (1 of 2 runs) | 10 (1 of 2 runs) | "
        "0.0010 (1 of 2 runs) |"
    ) in rendered
    assert (
        "Server usage, mean over 2 scored ON run(s): generation 1.0 request(s), input tokens 800 (1 of 2 runs), "
        "output tokens 800 (1 of 2 runs);"
    ) in rendered


class _FlushingClient:
    def __init__(
        self,
        cursors: list[tuple[int, int, int]],
        *,
        failing_flushes: frozenset[int] = frozenset(),
        unavailable_flushes: dict[int, str] | None = None,
    ) -> None:
        self._cursors = iter(cursors)
        self._failing_flushes = failing_flushes
        self._unavailable_flushes = unavailable_flushes or {}
        self.flushes = 0

    async def flush_memory(self, request):
        self.flushes += 1
        if self.flushes in self._failing_flushes:
            raise TimeoutError("flush timed out")  # noqa: TRY003
        if code := self._unavailable_flushes.get(self.flushes):
            raise UnavailableResponseError(status_code=503, request_id=None, code=code)
        previous, current, high = next(self._cursors)
        return SimpleNamespace(previous_cursor=previous, current_cursor=current, high_watermark=high)

    async def get_stats(self, request):
        assert request.selection.root.scope_ids[0].root == "scope-1"
        return SimpleNamespace(
            inventory=SimpleNamespace(
                sources=SimpleNamespace(total=3, memory_pending=0),
                memory=SimpleNamespace(entries=SimpleNamespace(total=2)),
            ),
            usage=SimpleNamespace(
                totals=SimpleNamespace(
                    generation=SimpleNamespace(requests=3, input_tokens=9870, output_tokens=640),
                    embedding=SimpleNamespace(requests=5, input_tokens=1210, output_tokens=None),
                )
            ),
            recall=SimpleNamespace(totals=SimpleNamespace(preparations=4, ready_preparations=1, recalled_tokens=1180)),
        )


def test_settling_flushes_until_the_scope_is_caught_up() -> None:
    client = _FlushingClient([(0, 1, 3), (1, 2, 3), (2, 3, 3)])

    snapshot = asyncio.run(settle_session(client, "scope-1", session=0, flush=True))

    assert client.flushes == 3
    assert snapshot == SessionSnapshot(
        session=0,
        flush_rounds=3,
        sources=3,
        memory_pending=0,
        memory_entries=2,
        preparations=4,
        ready_preparations=1,
        generation_requests=3,
        generation_input_tokens=9870,
        generation_output_tokens=640,
        embedding_requests=5,
        embedding_input_tokens=1210,
        recalled_tokens=1180,
    )


def test_settling_stops_when_a_flush_makes_no_progress() -> None:
    client = _FlushingClient([(0, 1, 3), (1, 1, 3), (1, 2, 3)])

    snapshot = asyncio.run(settle_session(client, "scope-1", session=1, flush=True))

    assert client.flushes == 2
    assert snapshot.flush_rounds == 2


def test_recorder_flushes_before_later_sessions_but_only_snapshots_the_final_one() -> None:
    client = _FlushingClient([(0, 1, 1)])
    recorder = SessionRecorder(client, "scope-1", final_session=1)

    asyncio.run(recorder(None))
    asyncio.run(recorder(None))

    assert client.flushes == 1
    assert [(snapshot.session, snapshot.flush_rounds) for snapshot in recorder.snapshots] == [(0, 1), (1, 0)]


def test_recorder_records_a_failed_settle_instead_of_raising() -> None:
    # Harbor awaits the hook in a finally block, where raising would replace a timed-out agent's own exception.
    recorder = SessionRecorder(_FlushingClient([], failing_flushes=frozenset({1})), "scope-1", final_session=1)

    asyncio.run(recorder(None))
    asyncio.run(recorder(None))

    assert recorder.failures == ["Settling the Scope after session 0 failed: TimeoutError: flush timed out"]
    assert [snapshot.session for snapshot in recorder.snapshots] == [1]
    assert any("not observed" in reason for reason in treatment_failures(recorder.snapshots, recall_session=1))


def test_settling_waits_while_a_plugin_flush_records_memory_owners(monkeypatch) -> None:
    # A host plugin's own flush can still be running when the harness settles; until it records who owns the Memory it
    # created, the Server answers 503 artifact_owner_pending, which is not an integration failure.
    monkeypatch.setattr(sessions_module, "OWNER_PENDING_DELAYS", (0.0, 0.0))
    pending = "artifact_owner_pending"
    recorder = SessionRecorder(
        _FlushingClient([(0, 1, 1)], unavailable_flushes={1: pending, 2: pending}), "scope-1", final_session=1
    )

    asyncio.run(recorder(None))
    asyncio.run(recorder(None))

    assert recorder.failures == []
    assert [(snapshot.session, snapshot.flush_rounds) for snapshot in recorder.snapshots] == [(0, 1), (1, 0)]


@pytest.mark.parametrize(
    ("unavailable_flushes", "code"),
    [
        ({1: "inference_timeout"}, "inference_timeout"),
        (
            {1: "artifact_owner_pending", 2: "artifact_owner_pending", 3: "artifact_owner_pending"},
            "artifact_owner_pending",
        ),
    ],
)
def test_settling_still_reports_an_unavailable_server(
    monkeypatch, unavailable_flushes: dict[int, str], code: str
) -> None:
    monkeypatch.setattr(sessions_module, "OWNER_PENDING_DELAYS", (0.0, 0.0))
    recorder = SessionRecorder(
        _FlushingClient([(0, 1, 1)], unavailable_flushes=unavailable_flushes), "scope-1", final_session=1
    )

    asyncio.run(recorder(None))

    assert recorder.failures == [
        "Settling the Scope after session 0 failed: "
        f"UnavailableResponseError: PowerContext Server returned HTTP 503 ({code})"
    ]


class _AnonymousClient:
    """Answer the harness's unauthenticated probe the way a Server with or without enforced access would."""

    answers = False

    def __init__(self, *_: object, **__: object) -> None:
        pass

    async def __aenter__(self) -> _AnonymousClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        del exc_info

    async def list_scopes(self) -> list[object]:
        if not self.answers:
            raise UnauthorizedResponseError(status_code=401, request_id=None)
        return []


def test_paired_accepts_a_server_that_requires_a_token(monkeypatch) -> None:
    monkeypatch.setattr(paired_module, "PowerContextClient", _AnonymousClient)

    asyncio.run(require_authenticated_server())


def test_paired_refuses_a_server_that_answers_without_a_token(monkeypatch) -> None:
    # Both arms can reach the Server, so an OFF agent could read the ON arm's Memory from an open Server.
    monkeypatch.setattr(paired_module, "PowerContextClient", type("OpenServer", (_AnonymousClient,), {"answers": True}))

    with pytest.raises(UnauthenticatedServerError):
        asyncio.run(require_authenticated_server())


def test_a_task_outcome_workload_is_scored_by_the_trial_reward() -> None:
    # A single-session task has no step results; Harbor records its verifier's reward on the trial.
    passed = HarborTrialObservation(rewards={"reward": 1})
    failed = HarborTrialObservation(rewards={"reward": 0.0})
    unscored = HarborTrialObservation()
    no_trial = HarborTrialObservation(exception_type="HarborJobError")

    outcomes = [
        arm_outcome((), harbor, scored_step=None, harness_failed=False, treatment_failures=())
        for harbor in (passed, failed, unscored, no_trial)
    ]

    assert outcomes == ["passed", "failed", "failed", "error"]


def test_task_outcome_workloads_score_their_single_session() -> None:
    tasks = load_tasks(_REPOSITORY / "e2e" / "bub" / "paired-tasks" / "swebench-pro")

    repositories = [category for task in tasks for category in task.categories if category.startswith("swebench-pro-")]
    assert len(repositories) == len(set(repositories)) == len(tasks) == 11
    assert {scored_session(task, HarnessSettings()) for task in tasks} == {ScoredSession(0, None)}
    assert all(task.dataset.name == "swebenchpro" and "paired" in task.categories for task in tasks)
    assert len({task.dataset.task_id for task in tasks}) == len(tasks)


def test_continuation_workloads_score_their_recall_step() -> None:
    (task,) = load_tasks(_REPOSITORY / "e2e" / "bub" / "paired-tasks" / "project-decision-continuation.yaml")

    assert scored_session(task, HarnessSettings()) == ScoredSession(1, "recall")


def test_a_run_whose_task_differs_from_the_manifest_is_reported() -> None:
    (task,) = load_tasks(_REPOSITORY / "e2e" / "bub" / "paired-tasks" / "project-decision-continuation.yaml")
    pinned = task.dataset.checksum

    assert checksum_failure(task, HarborTrialObservation(task_checksum=pinned)) is None
    assert checksum_failure(task, HarborTrialObservation()) is None
    failure = checksum_failure(task, HarborTrialObservation(task_checksum="0" * 64))
    assert failure is not None
    assert pinned in failure
    assert "0" * 64 in failure


def test_treatment_of_a_single_session_needs_capture_and_a_context_request_in_that_session() -> None:
    assert treatment_failures((_snapshot(0, asked=1, ready=0),), recall_session=0) == ()
    assert treatment_failures((), recall_session=0) == ("The Server was not observed after every session",)
    failures = treatment_failures((_snapshot(0, sources=0, memory=0, asked=0),), recall_session=0)
    assert failures == (
        "No Sources were captured during the session",
        "PowerContext was not asked for context during the session",
    )


def _task_outcome(task):
    return task.model_copy(update={"evaluation": TaskOutcomeComparisonSpec(comparison="task-outcome")})


def test_a_local_task_outcome_workload_runs_its_single_step_task(tmp_path: Path) -> None:
    task_dir = tmp_path / "tasks" / "single"
    (task_dir / "tests").mkdir(parents=True)
    (task_dir / "instruction.md").write_text("Fix it.")
    (task_dir / "task.toml").write_text('version = "1.3"\n')
    (task_dir / "tests" / "test.sh").write_text("echo 1 > /logs/verifier/reward.txt\n")
    (task,) = load_tasks(_REPOSITORY / "e2e" / "bub" / "paired-tasks" / "swebench-pro")[:1]
    local = _task_outcome(task).model_copy(
        update={
            "dataset": task.dataset.model_copy(
                update={
                    "name": None,
                    "version": None,
                    "path": Path("tasks"),
                    "task_id": "single",
                    "checksum": HarborTask(task_dir).checksum,
                }
            )
        }
    )

    assert scored_session(local, HarnessSettings(repository=tmp_path)) == ScoredSession(0, None)


def test_a_local_task_outcome_workload_cannot_have_steps() -> None:
    (continuation,) = load_tasks(_REPOSITORY / "e2e" / "bub" / "paired-tasks" / "project-decision-continuation.yaml")

    with pytest.raises(ValueError, match="has Harbor steps"):
        scored_session(_task_outcome(continuation), HarnessSettings())


class _ReadyClient:
    """A Server client whose readiness and capabilities checks pass."""

    async def __aenter__(self) -> _ReadyClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        del exc_info

    async def get_readiness(self) -> None:
        return None

    async def get_capabilities(self) -> SimpleNamespace:
        return SimpleNamespace(memory_extraction=True)


def test_a_workload_ends_after_harbor_ran_a_task_the_manifest_does_not_pin(monkeypatch, tmp_path: Path) -> None:
    # Every further trial of that workload would repeat the error at the cost of a full run; other workloads go on.
    stale, sound = load_tasks(_REPOSITORY / "e2e" / "bub" / "paired-tasks" / "swebench-pro")[:2]
    runs: list[tuple[str, int, str]] = []

    async def run_arm(client, task, *, trial, arm, output_dir, **kwargs):
        output_dir.mkdir(parents=True)
        runs.append((task.id, trial, arm))
        mismatch = "Harbor ran task checksum 0, not the manifest's 1" if task is stale else None
        return _observation(trial, arm, "error" if mismatch else "passed", task_id=task.id), mismatch

    async def authenticated() -> None:
        return None

    monkeypatch.setattr(paired_module, "_powercontext_client", _ReadyClient)
    monkeypatch.setattr(paired_module, "require_authenticated_server", authenticated)
    monkeypatch.setattr(paired_module, "require_runtime_models", lambda tasks, host: None)
    monkeypatch.setattr(paired_module, "_run_arm", run_arm)

    report = asyncio.run(run_paired((stale, sound), output_dir=tmp_path, settings=_SETTINGS, trials=2, host="pi"))

    assert runs == [
        (stale.id, 1, "off"),
        (sound.id, 1, "off"),
        (sound.id, 1, "on"),
        (sound.id, 2, "on"),
        (sound.id, 2, "off"),
    ]
    assert (report.tasks[0].off.errors, report.tasks[0].pairs) == (1, 0)
    assert report.tasks[1].pairs == 2
