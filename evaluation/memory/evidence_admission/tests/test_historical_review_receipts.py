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

import hashlib
import json

import pytest

from powercontext_eval_evidence_admission.historical_artifacts import REVIEWS_ENV, read_review_receipt
from powercontext_eval_evidence_admission.manifests import EvaluationError
from powercontext_eval_evidence_admission.v6 import contract as v6
from powercontext_eval_evidence_admission.v6 import workflow
from powercontext_eval_evidence_admission.v7 import contract as v7


def receipt(tmp_path, name="v6/recorded-atom-reviews.json"):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = b'{"original_manifest_hash":"synthetic","reviewer":"assistant","reviews":{}}\n'
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def test_current_protocol_and_dependency_identity_do_not_require_historical_receipts(monkeypatch, tmp_path):
    monkeypatch.delenv(REVIEWS_ENV, raising=False)
    contract = v6.protocol()
    original = v6.identity()
    assert original["historical_receipts"]["v6/recorded-atom-reviews.json"] == {
        "expected_sha256": contract["atom_reviews_sha256"]
    }
    assert "recorded-atom-reviews.json" not in original["files"]
    assert v7.identity()["v6_dependency"] == original
    monkeypatch.setenv(REVIEWS_ENV, str(tmp_path / "missing"))
    assert v6.identity() == original


def test_replay_requires_external_evidence_before_reading_session_or_writing_output(monkeypatch, tmp_path):
    monkeypatch.delenv(REVIEWS_ENV, raising=False)
    with pytest.raises(EvaluationError, match="historical_review_receipts_required"):
        workflow.replay_history(tmp_path / "absent-private-session.json", tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_v6_external_review_is_checked_against_the_contract(monkeypatch, tmp_path):
    path, checksum = receipt(tmp_path)
    monkeypatch.setenv(REVIEWS_ENV, str(tmp_path))
    monkeypatch.setattr(v6, "protocol", lambda: {"atom_reviews_sha256": checksum})
    assert v6.recorded_atom_reviews() == json.loads(path.read_bytes())
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(EvaluationError, match="historical_review_receipt_hash_mismatch"):
        v6.recorded_atom_reviews()


def test_external_receipt_missing_and_tampered_bytes_are_not_substituted(monkeypatch, tmp_path):
    path, checksum = receipt(tmp_path)
    monkeypatch.setenv(REVIEWS_ENV, str(tmp_path))
    expected = read_review_receipt("v6/recorded-atom-reviews.json", checksum)
    assert expected["reviewer"] == "assistant"
    path.unlink()
    with pytest.raises(EvaluationError, match="historical_review_receipt_unavailable"):
        read_review_receipt("v6/recorded-atom-reviews.json", checksum)


@pytest.mark.parametrize("name", ["../private.json", "/synthetic-private-2074/evidence.json"])
def test_external_receipt_cannot_escape_the_bundle(monkeypatch, tmp_path, name):
    monkeypatch.setenv(REVIEWS_ENV, str(tmp_path))
    with pytest.raises(EvaluationError, match="historical_review_receipt_invalid_path"):
        read_review_receipt(name, "a" * 64)


def test_external_receipt_symlink_cannot_escape_the_bundle(monkeypatch, tmp_path):
    path, checksum = receipt(tmp_path / "outside")
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "escaped.json").symlink_to(path)
    monkeypatch.setenv(REVIEWS_ENV, str(root))
    with pytest.raises(EvaluationError, match="historical_review_receipt_invalid_path"):
        read_review_receipt("escaped.json", checksum)


def test_looping_review_root_is_a_safe_configuration_error(monkeypatch, tmp_path):
    root = tmp_path / "private-review-location"
    root.symlink_to(root)
    monkeypatch.setenv(REVIEWS_ENV, str(root))
    with pytest.raises(EvaluationError, match="^historical_review_receipt_invalid_path$"):
        read_review_receipt("synthetic.json", "a" * 64)


def test_explicit_receipt_verifies_bytes_before_loading_json(tmp_path):
    path, checksum = receipt(tmp_path)
    assert read_review_receipt("synthetic.json", checksum, path=path)["original_manifest_hash"] == "synthetic"
    path.write_text('{"private-source-and-response-sentinel":')
    with pytest.raises(EvaluationError, match="historical_review_receipt_hash_mismatch"):
        read_review_receipt("synthetic.json", checksum, path=path)
    bad_json_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(EvaluationError, match="^historical_review_receipt_invalid_json$"):
        read_review_receipt("synthetic.json", bad_json_hash, path=path)
