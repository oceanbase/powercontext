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

import json
from dataclasses import replace
from functools import cache
from pathlib import Path

import pytest

from evaluation.memory.prepared_context_budget.experiment import CaseResult, decide, evaluate_case
from evaluation.memory.prepared_context_budget.manifest import Manifest, ManifestError, load_manifest, materialize
from evaluation.memory.prepared_context_budget.run import main

_SUITE = Path(__file__).resolve().parents[1]
_MANIFEST = _SUITE / "fixtures" / "budget-v1.json"
_PINNED = _SUITE / "reports" / "budget-v1"


@cache
def _manifest() -> Manifest:
    return load_manifest(_MANIFEST)


@cache
def _result(case_id: str) -> CaseResult:
    manifest = _manifest()
    return evaluate_case(manifest, manifest.case(case_id))


@pytest.mark.parametrize("case_id", [case.id for case in load_manifest(_MANIFEST).cases])
def test_every_fixture_case_passes_the_validity_checks(case_id: str) -> None:
    result = _result(case_id)

    assert result.failed_checks == ()
    assert result.baseline_content_bytes <= result.max_bytes
    assert result.candidate_content_bytes <= result.max_bytes


def test_the_pinned_report_replays_byte_for_byte(tmp_path: Path) -> None:
    assert main(["--manifest", str(_MANIFEST), "--output", str(tmp_path)]) == 0

    assert (tmp_path / "report.json").read_bytes() == (_PINNED / "report.json").read_bytes()
    assert (tmp_path / "report.md").read_bytes() == (_PINNED / "report.md").read_bytes()
    run = json.loads((tmp_path / "run-manifest.json").read_text(encoding="utf-8"))
    assert run["manifest_sha256"] == _manifest().sha256
    assert run["definitions_sha256"] == _manifest().definitions_sha256


def test_infeasible_minimums_are_reported_instead_of_overrunning_the_budget() -> None:
    # 16 candidates cannot all receive a 64-byte minimum inside 512 bytes once the envelope is counted.
    nothing = _result("default-infeasible-512")
    one = _result("default-infeasible-640")

    assert {item.candidate_disposition for item in nothing.items} == {"dropped_minimum_infeasible"}
    assert nothing.candidate_content_bytes == nothing.baseline_content_bytes == 0
    assert [item.candidate_disposition for item in one.items][:2] == ["truncated", "dropped_minimum_infeasible"]
    assert one.candidate_content_bytes <= 640


def test_short_items_stay_complete_and_only_truncations_keep_the_floor() -> None:
    result = _result("default-short-complete-items")
    short = [item for item in result.items if item.display_bytes < 64]

    # A complete short body is a valid delivery; it is admitted whole or not at all, never truncated.
    assert short
    assert {item.candidate_disposition for item in short} <= {"full", "dropped_minimum_infeasible"}
    assert any(item.candidate_disposition == "full" for item in short)
    for item in result.items:
        if item.candidate_disposition == "truncated":
            assert item.candidate_delivered_bytes >= 64


def test_upstream_caps_are_not_scored_as_budget_failures() -> None:
    result = _result("default-mixed-families")

    # The third experience is removed by the family cap before fitting; it is not a frozen candidate.
    assert "d6-e3" not in {item.artifact_id for item in result.items}


@pytest.mark.parametrize(
    ("delta", "loss", "outcome"),
    [
        (0.05, 0.0, "improvement_not_established"),
        (0.0500001, 0.0, "improvement_established"),
        (0.2, 0.2500001, "improvement_not_established"),
    ],
)
def test_the_decision_rule_uses_the_preregistered_thresholds(delta: float, loss: float, outcome: str) -> None:
    template = replace(_result("default-all-fit-control"), critical=False, fact_regressions=())
    result = replace(template, coverage_delta=delta, top_item_truncation_loss=loss)

    assert decide(_manifest(), [result]).outcome == outcome


def test_a_failed_validity_check_makes_the_comparison_invalid() -> None:
    template = _result("default-all-fit-control")
    broken = replace(template, checks={**template.checks, "candidate_within_max_bytes": False})

    decision = decide(_manifest(), [template, broken])

    assert decision.outcome == "invalid"
    assert decision.invalid_checks == ("default-all-fit-control: candidate_within_max_bytes",)


def test_sensitivity_cases_do_not_enter_the_decision() -> None:
    primary = replace(_result("default-all-fit-control"), coverage_delta=0.0)
    reordered = replace(primary, case_id="reordered", role="sensitivity", coverage_delta=1.0)

    assert decide(_manifest(), [primary, reordered]).mean_coverage_delta == 0.0


@pytest.mark.parametrize(
    ("spec", "size"),
    [
        ({"head": "H-", "fill": "记", "tail": "-T", "bytes": 64}, 64),
        ({"head": "H-", "fill": "🙂", "tail": "", "bytes": 63}, 63),
        ({"text": "literal"}, 7),
    ],
)
def test_bodies_materialize_at_their_declared_utf8_size(spec: dict[str, object], size: int) -> None:
    body = materialize(spec, "case")

    assert len(body.encode("utf-8")) == size
    assert body.startswith(str(spec.get("head", "")))
    assert body.endswith(str(spec.get("tail", "")))


def test_a_body_without_room_for_its_head_is_rejected() -> None:
    with pytest.raises(ManifestError):
        materialize({"head": "x" * 10, "fill": ".", "bytes": 4}, "case")
