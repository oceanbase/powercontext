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
from dataclasses import replace

import pytest
from typer.testing import CliRunner

from powercontext_eval_evidence_admission import cli, verification
from powercontext_eval_evidence_admission.cli import execute, manifest_for
from powercontext_eval_evidence_admission.manifests import EvaluationError, load_suite
from powercontext_eval_evidence_admission.report import aggregate, load_run, publish


@pytest.fixture
def frozen_runs(tmp_path, monkeypatch):
    suite = load_suite(experiment_version="v3")
    case = next(c for c in suite.cases if c.case_id == "small-complete")
    base = replace(
        suite, cases=(case,), experiment=suite.experiment.model_copy(update={"budgets": (4096,), "repetitions": 2})
    )
    monkeypatch.setattr(verification, "load_suite", lambda *args, dataset, **kwargs: replace(base, dataset=dataset))
    paths = []
    for dataset in base.experiment.v3.required_datasets:
        selected = replace(base, dataset=dataset)
        path = tmp_path / dataset
        asyncio.run(execute(selected, manifest_for(selected, "prepare"), path))
        paths.append(path)
    return base, tuple(paths)


def test_combined_gate_requires_every_frozen_dataset_and_never_authorizes_production(frozen_runs):
    suite, paths = frozen_runs
    result = verification.combined_preparation(suite, paths)
    assert result["offline_preparation_passed"]
    assert result["decision"] == "insufficient_evidence" and result["provider_requests"] == 0
    assert set(result["runs"]) == set(suite.experiment.v3.required_datasets)
    with pytest.raises(EvaluationError, match="missing_required_dataset"):
        verification.combined_preparation(suite, paths[:-1])
    with pytest.raises(EvaluationError, match="duplicate_dataset"):
        verification.combined_preparation(suite, (*paths, paths[0]))


def test_combined_gate_keeps_stress_loss_visible_when_primary_passes(frozen_runs, tmp_path):
    suite, paths = frozen_runs
    manifest, samples, _ = load_run(paths[-1])
    modified = []
    for sample in samples:
        if sample.arm == "bounded-fragments-v3":
            score = sample.preparation.model_copy(
                update={
                    "missing_claim_hashes": sample.preparation.retained_claim_hashes,
                    "retained_claim_hashes": (),
                }
            )
            sample = sample.model_copy(update={"preparation": score})
        modified.append(sample)
    selected = replace(suite, dataset=manifest["dataset"])
    path = tmp_path / "failing-stress"
    publish(path, manifest, tuple(modified), aggregate(selected, tuple(modified), manifest))
    result = verification.combined_preparation(suite, (*paths[:-1], path))
    assert result["runs"]["synthetic-v1"]["offline_preparation_passed"]
    assert not result["offline_preparation_passed"]
    assert result["runs"]["validation-v3"]["required_retention_failures"]
    assert result["runs"]["validation-v3"]["critical_failures"]


def test_compare_command_writes_complete_result_and_refuses_overwrite(frozen_runs, tmp_path, monkeypatch):
    suite, paths = frozen_runs
    monkeypatch.setattr(cli, "load_suite", lambda *args, **kwargs: suite)
    output = tmp_path / "comparison.json"
    arguments = ["compare-preparation", "--output-file", str(output)]
    for path in paths:
        arguments.extend(["--run-dir", str(path)])
    runner = CliRunner()
    result = runner.invoke(cli.app, arguments)
    assert result.exit_code == 0, result.exception
    assert '"offline_preparation_passed": true' in output.read_text()
    before = output.read_bytes()
    assert runner.invoke(cli.app, arguments).exit_code != 0
    assert output.read_bytes() == before


def test_combined_gate_rejects_unexpected_unavailable_in_all_arms(frozen_runs, tmp_path):
    suite, paths = frozen_runs
    manifest, samples, _ = load_run(paths[-1])
    modified = tuple(
        s.model_copy(update={"preparation": None, "status": "unavailable", "completeness": "unavailable"})
        for s in samples
    )
    selected = replace(suite, dataset=manifest["dataset"])
    path = tmp_path / "unavailable-stress"
    publish(path, manifest, modified, aggregate(selected, modified, manifest))
    result = verification.combined_preparation(suite, (*paths[:-1], path))
    assert not result["offline_preparation_passed"]
    assert result["runs"]["validation-v3"]["preparation_outcome_failures"]
