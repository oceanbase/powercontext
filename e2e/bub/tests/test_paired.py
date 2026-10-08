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
import runpy
import shutil
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from harbor.models.task.task import Task as HarborTask
from harbor.models.trial.result import StepResult
from harbor.models.verifier.result import VerifierResult
from powercontext.client import UnauthorizedResponseError

from powercontext_e2e import paired as paired_module
from powercontext_e2e.catalog import load_tasks
from powercontext_e2e.models import (
    HarborTrialObservation,
    PairedAgent,
    PairedArmObservation,
    RunEnvironment,
    SessionSnapshot,
)
from powercontext_e2e.paired import (
    UnauthenticatedServerError,
    arm_outcome,
    classify_outcome,
    recall_session_index,
    require_authenticated_server,
    summarize,
    treatment_failures,
)
from powercontext_e2e.runner import run_tasks
from powercontext_e2e.sessions import SessionRecorder, settle_session
from powercontext_e2e.settings import HarnessSettings

_REPOSITORY = Path(__file__).resolve().parents[3]
_PAIRED_TASKS = load_tasks(_REPOSITORY / "e2e" / "bub" / "paired-tasks")
_HARBOR_TASKS = _REPOSITORY / "e2e" / "bub" / "harbor-tasks"
_SETTINGS = HarnessSettings(repository=_REPOSITORY)


def _grade(answer_path: Path, reward_path: Path) -> None:
    # run_path does not write bytecode, which would change the Harbor task checksum.
    grader = runpy.run_path(
        str(_HARBOR_TASKS / "project-decision-continuation" / "steps" / "recall" / "tests" / "grade.py")
    )
    grader["main"](answer_path, reward_path)


@pytest.mark.parametrize(
    ("answer", "reward"),
    [
        ('{"database": "OceanBase", "shard_count": 12}', 1),
        ('{"database": " oceanbase ", "shard_count": "12"}', 1),
        # Contradictory answers name the right fact somewhere but assert another value.
        ('{"database": "OceanBase", "shard_count": 24}', 0),
        ('{"database": "PostgreSQL", "shard_count": 12}', 0),
        ("We chose OceanBase with 24 shards, not 12.", 0),
        ("We chose PostgreSQL rather than OceanBase, with 12 shards.", 0),
        # Uncertain answers do not assert the decision.
        ('{"database": null, "shard_count": null}', 0),
        ('{"database": "OceanBase?", "shard_count": 12}', 0),
        ('{"database": "maybe OceanBase", "shard_count": 12}', 0),
        ('{"database": "OceanBase", "shard_count": "about 12"}', 0),
        ('["OceanBase", 12]', 0),
        # A repeated key or an extra field could hide a contradiction from the checked values.
        ('{"database": "PostgreSQL", "database": "OceanBase", "shard_count": 12}', 0),
        ('{"database": "OceanBase", "shard_count": 12, "note": "or PostgreSQL with 24"}', 0),
    ],
)
def test_recall_grader_checks_the_asserted_values(tmp_path: Path, answer: str, reward: int) -> None:
    answer_path = tmp_path / "answer.json"
    answer_path.write_text(answer, encoding="utf-8")
    reward_path = tmp_path / "reward.txt"

    _grade(answer_path, reward_path)

    assert reward_path.read_text(encoding="utf-8") == f"{reward}\n"


def test_recall_grader_scores_a_missing_answer_as_zero(tmp_path: Path) -> None:
    reward_path = tmp_path / "reward.txt"

    _grade(tmp_path / "answer.json", reward_path)

    assert reward_path.read_text(encoding="utf-8") == "0\n"


@pytest.mark.parametrize("task", _PAIRED_TASKS, ids=lambda task: task.id)
def test_continuation_tasks_hide_the_answer_until_the_recall_session(task) -> None:
    # Harbor uploads shared tests before every step and leaves them in the container, so an answer key there would
    # be readable in the earlier session.
    assert recall_session_index(task, _SETTINGS) >= 1
    assert not (_HARBOR_TASKS / task.dataset.task_id / "tests").exists()


def test_recall_step_must_be_the_final_session() -> None:
    task = _PAIRED_TASKS[0]
    task = task.model_copy(update={"evaluation": task.evaluation.model_copy(update={"recall_step": "capture"})})

    with pytest.raises(ValueError, match="must end with its recall step"):
        recall_session_index(task, _SETTINGS)


def test_acceptance_rejects_continuation_workloads(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="paired command"):
        asyncio.run(run_tasks(_PAIRED_TASKS, output_dir=tmp_path / "out", settings=_SETTINGS))


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

    outcome = arm_outcome(steps, averaged, recall_step="recall", harness_failed=False, treatment_failures=())

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


def _observation(trial: int, arm: str, outcome: str, task_id: str = "task") -> PairedArmObservation:
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
    assert report.total.off.errors == 1


class _FlushingClient:
    def __init__(self, cursors: list[tuple[int, int, int]], *, failing_flushes: frozenset[int] = frozenset()) -> None:
        self._cursors = iter(cursors)
        self._failing_flushes = failing_flushes
        self.flushes = 0

    async def flush_memory(self, request):
        self.flushes += 1
        if self.flushes in self._failing_flushes:
            raise TimeoutError("flush timed out")  # noqa: TRY003
        previous, current, high = next(self._cursors)
        return SimpleNamespace(previous_cursor=previous, current_cursor=current, high_watermark=high)

    async def get_stats(self, request):
        assert request.selection.root.scope_ids[0].root == "scope-1"
        return SimpleNamespace(
            inventory=SimpleNamespace(
                sources=SimpleNamespace(total=3, memory_pending=0),
                memory=SimpleNamespace(entries=SimpleNamespace(total=2)),
            ),
            recall=SimpleNamespace(totals=SimpleNamespace(preparations=4, ready_preparations=1)),
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
