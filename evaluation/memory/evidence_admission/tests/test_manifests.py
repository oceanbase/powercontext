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
import shutil

import pytest

from powercontext_eval_evidence_admission.historical_artifacts import RESULTS_ENV, historical_results_root
from powercontext_eval_evidence_admission.manifests import (
    PACKAGE_ROOT,
    EvaluationError,
    file_hash,
    load_private_suite,
    load_suite,
)


def test_historical_results_bundle_is_explicit_and_local(tmp_path, monkeypatch):
    monkeypatch.delenv(RESULTS_ENV, raising=False)
    with pytest.raises(EvaluationError, match="historical_results_bundle_required"):
        historical_results_root()
    monkeypatch.setenv(RESULTS_ENV, str(tmp_path / "missing"))
    with pytest.raises(EvaluationError, match="historical_results_bundle_unavailable"):
        historical_results_root()
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setenv(RESULTS_ENV, str(results))
    assert historical_results_root() == results


def test_frozen_suite_covers_declared_matrix(suite):
    categories = {label for case in suite.cases for label in case.categories}
    assert {
        "single-overflow",
        "many-overflow",
        "mixed",
        "positional",
        "utf8",
        "exact-literals",
        "negative",
        "impossible",
        "replay",
        "cancellation",
    } <= categories
    assert {case.cancel_phase for case in suite.cases} >= {"before_read", "projection", "generation"}
    assert any(source.route == "observation" for case in suite.cases for source in case.sources)


def test_v2_freezes_additional_arm_and_validation_without_changing_v1_inputs(suite):
    v2 = load_suite(experiment_version="v2")
    validation = load_suite(experiment_version="v2", dataset="validation-v2")
    assert v2.cases == suite.cases and v2.decision == suite.decision
    assert v2.experiment.candidate_arm == "bounded-fragments-v2"
    assert v2.experiment.arms[:-1] == suite.experiment.arms
    assert v2.experiment.v2.reference_encoding == "v1-claim-fragments"
    assert len(validation.cases) == 8 and validation.dataset == "validation-v2"
    assert not {c.case_id for c in validation.cases} & {c.case_id for c in v2.cases}
    assert v2.frozen_hashes == validation.frozen_hashes


def test_v2_validation_content_must_match_frozen_hashes(tmp_path):
    shutil.copytree(PACKAGE_ROOT / "manifests", tmp_path / "manifests")
    shutil.copytree(PACKAGE_ROOT / "fixtures", tmp_path / "fixtures")
    path = tmp_path / "fixtures/validation-v2.json"
    path.write_text(path.read_text() + " ")
    with pytest.raises(EvaluationError, match="hash_mismatch"):
        load_suite(tmp_path, experiment_version="v2")
    with pytest.raises(EvaluationError, match="invalid_experiment_policy"):
        load_suite(dataset="validation-v2")


def test_fixture_or_threshold_edits_require_new_frozen_hashes(tmp_path):
    shutil.copytree(PACKAGE_ROOT / "manifests", tmp_path / "manifests")
    shutil.copytree(PACKAGE_ROOT / "fixtures", tmp_path / "fixtures")
    path = tmp_path / "manifests/decision-policy-v1.json"
    value = json.loads(path.read_text())
    value["critical_unsupported_max"] = 1
    path.write_text(json.dumps(value))
    with pytest.raises(EvaluationError, match="hash_mismatch"):
        load_suite(tmp_path)


def test_private_manifest_reads_only_explicit_hash_pinned_external_file(suite, small_case, tmp_path, monkeypatch):
    private = tmp_path / "case.json"
    private.write_text(small_case.model_dump_json(), encoding="utf-8")
    monkeypatch.setenv("EVIDENCE_CASE", str(private))
    path = tmp_path / "manifest.json"
    value = {
        "schema": "evidence-admission.private.v1",
        "decision_sha256": suite.experiment.decision_sha256,
        "cases": [{"label": small_case.case_id, "file_env": "EVIDENCE_CASE", "sha256": file_hash(private)}],
    }
    path.write_text(json.dumps(value))
    loaded = load_private_suite(path, suite)
    assert loaded.private and loaded.cases == (small_case,)
    private.write_text("secret invalid private material")
    with pytest.raises(EvaluationError, match="hash_mismatch") as error:
        load_private_suite(path, suite)
    assert "secret" not in str(error.value)


def test_v3_preserves_v2_inputs_and_pins_all_required_datasets():
    old = load_suite(experiment_version="v2")
    current = load_suite(experiment_version="v3")
    stress = load_suite(experiment_version="v3", dataset="validation-v3")
    assert current.cases == old.cases and current.decision == old.decision
    assert current.experiment.v2 == old.experiment.v2
    assert current.experiment.arms[:-1] == old.experiment.arms
    assert current.experiment.candidate_arm == "bounded-fragments-v3"
    assert current.experiment.v3.required_datasets == ("synthetic-v1", "validation-v2", "validation-v3")
    assert len(stress.cases) == 8 and stress.frozen_hashes == current.frozen_hashes
    assert not {c.case_id for c in stress.cases} & {c.case_id for c in current.cases}


def test_v3_additional_fixture_cannot_change_after_freeze(tmp_path):
    shutil.copytree(PACKAGE_ROOT / "manifests", tmp_path / "manifests")
    shutil.copytree(PACKAGE_ROOT / "fixtures", tmp_path / "fixtures")
    path = tmp_path / "fixtures/validation-v3.json"
    path.write_text(path.read_text() + " ")
    with pytest.raises(EvaluationError, match="hash_mismatch"):
        load_suite(tmp_path, experiment_version="v3")
    with pytest.raises(EvaluationError, match="invalid_experiment_policy"):
        load_suite(experiment_version="v2", dataset="validation-v3")


def test_live_policy_cannot_disable_frozen_gates(tmp_path):
    shutil.copytree(PACKAGE_ROOT / "manifests", tmp_path / "manifests")
    shutil.copytree(PACKAGE_ROOT / "fixtures", tmp_path / "fixtures")
    path = tmp_path / "manifests/live-policy-v1.json"
    value = json.loads(path.read_text())
    value["require_original_and_audited_runs"] = False
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(EvaluationError, match="live_policy_freeze_mismatch"):
        load_suite(tmp_path, experiment_version="v3")
