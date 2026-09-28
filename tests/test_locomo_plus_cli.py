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

"""CLI boundaries that prevent accidental paid or unbounded benchmark execution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from benchmark.locomo.dataset import LoCoMoConversation, LoCoMoSession, LoCoMoTurn
from benchmark.locomo_plus import cli
from benchmark.locomo_plus.dataset import DEFAULT_SMOKE_PATH, SMOKE_CASE_IDS, LoCoMoPlusCase, LoCoMoPlusDataset


@pytest.fixture
def dataset() -> LoCoMoPlusDataset:
    sessions = tuple(
        LoCoMoSession(
            session_id=f"D{number}",
            session_number=number,
            date_time=f"1:00 pm on {number} May, 2023",
            turns=(LoCoMoTurn(dialogue_id=f"D{number}:1", speaker="A", text="I need a quiet place."),),
        )
        for number in range(1, 4)
    )
    conversation = LoCoMoConversation(
        sample_id="synthetic", speaker_a="A", speaker_b="B", sessions=sessions, questions=()
    )
    cases = tuple(
        LoCoMoPlusCase(
            case_id=case_id,
            sample_id=f"synthetic-{relation}",
            category=6,
            relation_type=relation,
            question="Where should I work?",
            answer="A quiet place.",
            evidence=("D2:1",),
            evidence_text="A: I need a quiet place.",
            conversation=conversation,
            cue_session_id="D2",
            query_time="1:00 pm on 4 May, 2023",
            metadata={},
        )
        for case_id, relation in zip(SMOKE_CASE_IDS, ("causal", "state", "goal", "value"), strict=True)
    )
    return LoCoMoPlusDataset(
        cases=cases,
        exclusions=(),
        manifest={
            "commit": "synthetic",
            "seed": 42,
            "eligible_count": 4,
            "excluded_count": 0,
            "files": {},
        },
    )


def test_paid_run_requires_explicit_judge_before_loading_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_load(_: Any) -> LoCoMoPlusDataset:
        pytest.fail("a missing judge must be rejected before any dataset download")

    monkeypatch.setattr(cli, "_load_dataset", unexpected_load)
    with pytest.raises(SystemExit, match="2"):
        cli.main(["run"])


@pytest.mark.parametrize(
    "command", [["inspect"], ["run", "--dry-run"], ["run", "--profile", "full", "--limit", "1", "--dry-run"]]
)
def test_inspection_and_planning_need_no_credentials_or_models(
    command: list[str],
    dataset: LoCoMoPlusDataset,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "_load_dataset", lambda _: dataset)

    def unexpected_service(*_: Any, **__: Any) -> Any:
        pytest.fail("inspect and dry-run must not load provider settings or make paid calls")

    monkeypatch.setattr(cli, "load_settings", unexpected_service)
    monkeypatch.setattr(cli, "run_benchmark", unexpected_service)
    assert cli.main(command) == 0
    output = json.loads(capsys.readouterr().out)
    if command == ["inspect"]:
        assert output["dataset"]["eligible_count"] == 4
    else:
        assert output["dry_run"] is True
        assert output["plan"]


@pytest.mark.parametrize("flag", ["--limit", "--top-k", "--max-tokens", "--max-history-sessions"])
def test_nonpositive_resource_bounds_are_rejected(flag: str) -> None:
    with pytest.raises(SystemExit, match="2"):
        cli.main(["run", "--dry-run", flag, "0"])


def test_full_profile_passes_explicit_case_and_history_bounds(
    dataset: LoCoMoPlusDataset,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "_load_dataset", lambda _: dataset)
    monkeypatch.setattr(cli, "load_settings", lambda _: object())
    received: dict[str, Any] = {}

    async def run(selected_dataset: LoCoMoPlusDataset, **options: Any) -> dict[str, Any]:
        received.update(options)
        count = len(selected_dataset.selected_cases(mode=options["profile"], limit=options["limit"]))
        return {"planned_cases": count, "overall": {"failure_count": 0, "unobserved_count": 0}}

    monkeypatch.setattr(cli, "run_benchmark", run)
    assert (
        cli.main([
            "run",
            "--profile",
            "full",
            "--limit",
            "1",
            "--max-history-sessions",
            "1",
            "--judge-model",
            "openai:test-judge",
            "--arm",
            "query-only",
            "--output-directory",
            str(tmp_path),
            "--run-id",
            "bounded-full",
        ])
        == 0
    )
    assert received["profile"] == "full"
    assert received["limit"] == 1
    assert received["max_history_sessions"] == 1
    assert received["judge_model"] == "openai:test-judge"
    assert received["arm"] == "query-only"
    assert '"planned_cases": 1' in capsys.readouterr().out


@pytest.mark.parametrize(
    ("failure_count", "unobserved_count", "wrong_count", "exit_code"),
    [(1, 0, 0, 1), (0, 1, 0, 1), (0, 0, 1, 0)],
)
def test_run_exit_status_distinguishes_execution_failures_from_wrong_answers(
    failure_count: int,
    unobserved_count: int,
    wrong_count: int,
    exit_code: int,
    dataset: LoCoMoPlusDataset,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "_load_dataset", lambda _: dataset)
    monkeypatch.setattr(cli, "load_settings", lambda _: object())

    async def run(*_: Any, **__: Any) -> dict[str, Any]:
        return {
            "overall": {
                "failure_count": failure_count,
                "unobserved_count": unobserved_count,
                "wrong_count": wrong_count,
            }
        }

    monkeypatch.setattr(cli, "run_benchmark", run)
    assert (
        cli.main(["run", "--judge-model", "openai:test-judge", "--limit", "1", "--output-directory", str(tmp_path)])
        == exit_code
    )


def test_replay_needs_no_dataset_or_provider_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def unexpected_service(*_: Any, **__: Any) -> Any:
        pytest.fail("replay must use only saved observations")

    monkeypatch.setattr(cli, "_load_dataset", unexpected_service)
    monkeypatch.setattr(cli, "load_settings", unexpected_service)
    monkeypatch.setattr(cli, "run_benchmark", unexpected_service)
    monkeypatch.setattr(cli, "replay_results", lambda _: {"completed": 1})
    assert cli.main(["replay", "--run-directory", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out) == {"summary": {"completed": 1}}


@pytest.mark.parametrize("arm", ["memory", "full-context"])
def test_bundled_ten_case_plan_is_offline_and_preserves_complete_histories(
    arm: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def unexpected_service(*_: Any, **__: Any) -> Any:
        pytest.fail("bundled dry runs must not download data or call model services")

    monkeypatch.setattr(cli, "ensure_dataset", unexpected_service)
    monkeypatch.setattr(cli, "load_settings", unexpected_service)
    monkeypatch.setattr(cli, "run_benchmark", unexpected_service)
    assert cli.main(["run", "--dataset-file", str(DEFAULT_SMOKE_PATH), "--limit", "10", "--arm", arm, "--dry-run"]) == 0
    plan = json.loads(capsys.readouterr().out)["plan"]
    snapshot = json.loads(DEFAULT_SMOKE_PATH.read_text(encoding="utf-8"))
    assert plan["selected_count"] == 10
    assert plan["case_ids"] == snapshot["manifest"]["selected_case_ids"]
    assert plan["histories"] == snapshot["manifest"]["histories"]
    assert sum(history["session_count"] for history in plan["histories"].values()) == 253
    assert plan["history_truncated"] is False
    assert plan["scope"] == "subset"


@pytest.mark.parametrize("options", [["--profile", "full"], ["--seed", "17"], ["--limit", "11"]])
def test_bundled_input_rejects_requests_outside_its_fixed_scope(options: list[str]) -> None:
    with pytest.raises(SystemExit, match="2"):
        cli.main(["run", "--dataset-file", str(DEFAULT_SMOKE_PATH), "--dry-run", *options])
