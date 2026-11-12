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

"""Historical content checks remain strict when execution commit metadata changes."""

import copy
from pathlib import Path

import pytest

from powercontext_eval_evidence_admission.manifests import EvaluationError, read_json
from powercontext_eval_evidence_admission.v11 import workflow
from powercontext_eval_evidence_admission.v11.lineage import content_identity

BASELINE = Path(__file__).resolve().parents[1] / "results/v10-offline"


def with_execution_change(value):
    if isinstance(value, list):
        return [with_execution_change(v) for v in value]
    if not isinstance(value, dict):
        return value
    result = {k: with_execution_change(v) for k, v in value.items()}
    if {"suite_code_hash", "runtime_code_hash"} <= result.keys():
        result.update(revision="another-execution-commit", frozen_committed=False)
    return result


def test_content_identity_preserves_contract_revision_and_every_code_hash():
    original = {
        "contract": {"revision": "policy-v1"},
        "code": {
            "suite_code_hash": "suite",
            "runtime_code_hash": "runtime",
            "revision": "commit-a",
            "frozen_committed": True,
        },
    }
    assert content_identity(original) == content_identity(with_execution_change(original))
    changed = copy.deepcopy(original)
    changed["code"]["runtime_code_hash"] = "changed-runtime"
    assert content_identity(original) != content_identity(changed)
    changed = copy.deepcopy(original)
    changed["contract"]["revision"] = "policy-v2"
    assert content_identity(original) != content_identity(changed)


def test_archived_baseline_accepts_execution_metadata_changes_without_rewriting_history(monkeypatch):
    historical = read_json(BASELINE / "comparison.json")
    original_hash = workflow.file_hash(BASELINE / "comparison.json")
    monkeypatch.setattr(workflow, "v10_identity", lambda: with_execution_change(historical["identity"]))
    assert workflow.baseline(BASELINE) == historical
    assert workflow.file_hash(BASELINE / "comparison.json") == original_hash


def test_archived_baseline_rejects_current_source_changes_even_when_metadata_is_ignored(monkeypatch):
    changed = with_execution_change(read_json(BASELINE / "comparison.json")["identity"])
    changed["files"]["protocol.py"] = "different-source-hash"
    monkeypatch.setattr(workflow, "v10_identity", lambda: changed)
    with pytest.raises(EvaluationError, match="v11_baseline_identity_or_sample_set_changed"):
        workflow.baseline(BASELINE)
