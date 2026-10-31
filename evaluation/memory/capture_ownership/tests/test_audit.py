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

import importlib.util
from pathlib import Path

import pytest

from powercontext_eval_capture_ownership.models import ARMS, Manifest, RunConfig, fingerprint, read_json, write_json
from powercontext_eval_capture_ownership.report import review_template


@pytest.fixture(scope="module")
def audit():
    path = Path(__file__).resolve().parents[1] / "scripts/audit_synthetic.py"
    spec = importlib.util.spec_from_file_location("capture_ownership_synthetic_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.audit


def write_run(directory, manifest, historical_text, unsupported):
    case = next(case for case in manifest.cases if case.case_id == "repeated-history")
    manifest = Manifest(schema_version="capture-ownership.manifest.v1", corpus="synthetic", cases=(case,))
    config = RunConfig(schema_version="capture-ownership.config.v1")
    lock = {
        "manifest_sha256": fingerprint(manifest.model_dump(mode="json")),
        "config": config.model_dump(mode="json"),
        "code_sha": "a" * 40,
        "prompt_version": "powercontext.atomic-memory.extract.coding.v1",
        "prompt_sha256": "b" * 64,
        "file_sha256": {},
        "decision_policy": {"critical_repetitions": 2},
    }
    current = next(claim for claim in case.claims if claim.required_current)
    rows = []
    for repetition in range(2):
        for arm in ARMS:
            candidates = [{"kind": "constraint", "text": current.text, "evidence_ids": ["source-1"]}]
            if arm == ARMS[0]:
                candidates.append({"kind": "constraint", "text": historical_text, "evidence_ids": ["source-1"]})
            rows.append(
                {
                    "case_id": case.case_id,
                    "arm": arm,
                    "repetition": repetition,
                    "status": "completed",
                    "turn_status": "eligible",
                    "turn_reason": "validated_host_envelope",
                    "parser_matches_ground_truth": True,
                    "source_sha256": "c" * 64,
                    "materialization_sha256": "d" * 64,
                    "source_position": 1,
                    "evidence_id": "source-1",
                    "input": {},
                    "input_sha256": fingerprint({}),
                    "candidates": candidates,
                    "output_sha256": fingerprint(candidates),
                    "occupancy": {
                        "labeled_utf8_bytes": {"current": 10},
                        "text_carriers": 1,
                        "serialized_utf8_bytes": 10,
                    },
                    "estimated_input_tokens": 10,
                    "generation_seconds": 0.1,
                    "provider_usage": None,
                }
            )
    run = {"complete": True, "lock_sha256": fingerprint(lock), "mode": "live", "rows": rows}
    review = review_template(run)
    for row in review["cases"]:
        row.update(complete=True, reviewer="synthetic-test-reviewer")
        for index, label in enumerate(row["candidates"]):
            label.update(
                claim_ids=[current.claim_id] if index == 0 else [] if unsupported else ["archive-retention"],
                support_complete=True,
                unsupported_claim=index == 1 and unsupported,
                current_user_attribution=False,
            )
    for name, value in [
        ("manifest.json", manifest.model_dump(mode="json")),
        ("lock.json", lock),
        ("run.json", run),
        ("review.json", review),
    ]:
        write_json(directory / name, value)


@pytest.mark.parametrize(
    "text", ["The legacy archive retains backups for 37 days.", "The archive has a 37-day retention."]
)
def test_complete_supported_canary_values_produce_witnesses(manifest, tmp_path, audit, text):
    write_run(tmp_path, manifest, text, unsupported=False)
    result = audit(tmp_path)
    assert len(result["entries"]) == 2
    assert all(entry["claim_id"] == "archive-retention" for entry in result["entries"])


@pytest.mark.parametrize("value", ["370", "137", "37.5", "0.37", "37,000", "-37", "37e2"])
def test_extended_numeric_values_cannot_be_canary_witnesses(manifest, tmp_path, audit, value):
    # A mistaken assisted label must not bypass the deterministic exact-value check.
    write_run(tmp_path, manifest, f"The legacy archive retains backups for {value} days.", unsupported=False)
    with pytest.raises(ValueError, match="does not cover every reported reproduction"):
        audit(tmp_path)


def test_truthfully_unsupported_extended_value_is_not_a_witness(manifest, tmp_path, audit):
    write_run(tmp_path, manifest, "The legacy archive retains backups for 370 days.", unsupported=True)
    # Unsupported-only failures no longer qualify as ownership reproductions.
    assert audit(tmp_path)["entries"] == []


def test_matching_number_with_unsupported_units_is_not_a_claim_witness(manifest, tmp_path, audit):
    write_run(tmp_path, manifest, "The legacy archive retains backups for 37 hours.", unsupported=True)
    assert audit(tmp_path)["entries"] == []


def test_supported_canary_does_not_attest_unrelated_unsupported_assertions(manifest, tmp_path, audit):
    write_run(
        tmp_path, manifest, "The legacy archive retains backups for 37 days. An unrelated invented assertion.", False
    )
    review = read_json(tmp_path / "review.json")
    for row in review["cases"]:
        if row["arm"] == ARMS[0]:
            row["candidates"][1]["unsupported_claim"] = True
    write_json(tmp_path / "review.json", review)
    assert len(audit(tmp_path)["entries"]) == 2
