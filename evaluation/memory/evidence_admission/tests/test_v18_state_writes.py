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

"""Durable state publication and immutable audit records across interrupted writers."""

from __future__ import annotations

import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from powercontext_eval_evidence_admission.manifests import EvaluationError, read_json
from powercontext_eval_evidence_admission.v18.core import dump


def test_state_write_ignores_and_preserves_abandoned_temporaries(tmp_path):
    path = tmp_path / "state.json"
    dump(path, {"state": "scoring_pending", "score": None})
    abandoned = {
        tmp_path / "state.json.tmp": b"incomplete JSON",
        tmp_path / ".state.json.abandoned.tmp": b'{"state":"finished","score":"untrusted"}',
    }
    for temporary, value in abandoned.items():
        temporary.write_bytes(value)

    dump(path, {"state": "ready", "score": 1})

    assert read_json(path) == {"state": "ready", "score": 1}
    assert path.stat().st_mode & 0o777 == 0o600
    assert set(tmp_path.iterdir()) == {path, *abandoned}
    for temporary, value in abandoned.items():
        assert temporary.read_bytes() == value


@pytest.mark.parametrize("new", [False, True], ids=["state", "immutable-audit"])
def test_invalid_json_does_not_publish_or_leave_a_temporary(tmp_path, new):
    path = tmp_path / "record.json"
    if not new:
        dump(path, {"score": 1})
    original = path.read_bytes() if path.exists() else None

    with pytest.raises(ValueError, match="Out of range float"):
        dump(path, {"score": float("nan")}, new=new)

    if new:
        assert not path.exists()
        assert list(tmp_path.iterdir()) == []
    else:
        assert path.read_bytes() == original
        assert list(tmp_path.iterdir()) == [path]


def test_failed_state_publication_keeps_the_committed_state(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    dump(path, {"state": "scoring_pending"})
    original = path.read_bytes()

    def refuse_replace(_source, _target):
        raise PermissionError("synthetic publication failure")

    monkeypatch.setattr(os, "replace", refuse_replace)
    with pytest.raises(PermissionError, match="synthetic publication failure"):
        dump(path, {"state": "ready"})

    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_existing_immutable_record_cannot_be_overwritten(tmp_path):
    path = tmp_path / "review.json"
    dump(path, {"review": "original"}, new=True)
    original = path.read_bytes()

    with pytest.raises(EvaluationError, match="v18_output_exists"):
        dump(path, {"review": "replacement"}, new=True)

    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_concurrent_immutable_records_have_one_winner(tmp_path, monkeypatch):
    path = tmp_path / "review.json"
    barrier = Barrier(2)
    mkstemp = tempfile.mkstemp

    def synchronize_temporary(**kwargs):
        result = mkstemp(**kwargs)
        barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(tempfile, "mkstemp", synchronize_temporary)

    def publish(value):
        try:
            dump(path, {"review": value}, new=True)
        except EvaluationError as error:
            assert str(error) == "v18_output_exists"
            return None
        return value

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(publish, ["first", "second"]))

    assert results.count(None) == 1
    winner = next(value for value in results if value is not None)
    assert read_json(path) == {"review": winner}
    assert path.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.iterdir()) == [path]
