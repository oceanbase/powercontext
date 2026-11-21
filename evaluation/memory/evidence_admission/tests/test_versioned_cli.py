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

"""Versioned CLI diagnostics never include saved private values or filesystem paths."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys

import pytest

from powercontext_eval_evidence_admission.manifests import EvaluationError
from powercontext_eval_evidence_admission.models import digest
from powercontext_eval_evidence_admission.v18 import core

PRIVATE_VALUE = "SYNTHETIC_PRIVATE_EVIDENCE_2074"
PRIVATE_PATH = "/synthetic-private-2074/evidence.json"


@pytest.fixture(params=["v18", "v19", "v20"])
def versioned_cli(request):
    version = request.param
    return version, importlib.import_module(f"powercontext_eval_evidence_admission.{version}.__main__")


@pytest.fixture(scope="module")
def identity():
    return core.identity()


def saved_session(directory, version, identity, monkeypatch):
    manifest = {
        "identity": identity,
        "identity_hash": digest(identity),
        "reliability_passed": True,
        "rows": [{"row_id": "synthetic-row", "sample": {"input_hashes": ["synthetic-input"]}}],
    }
    core.dump(directory / "preparation.json", manifest)
    if version != "v18":
        workflow = importlib.import_module(f"powercontext_eval_evidence_admission.{version}.workflow")
        policy = {**workflow.policy(), "attempted_prefix": 0, "original_records_hash": digest([])}
        monkeypatch.setattr(workflow, "policy", lambda: policy)
        continuation = {
            "contract": policy,
            "preparation_hash": digest(manifest),
            "remaining_calls": [{"row_id": "synthetic-row", "input_index": 0, "input_hash": "synthetic-input"}],
        }
        core.dump(directory / "continuation.json", continuation)
        core.dump(
            directory / "state.json",
            {
                "state": "prepared",
                "records": [],
                "continuation_hash": digest(continuation),
            },
        )
    return directory / "inputs/synthetic-row.json"


@pytest.mark.parametrize("defect", ["validation", "missing-field", "wrong-shape", "missing-index", "invalid-json"])
def test_malformed_saved_input_has_only_safe_cli_diagnostics(
    versioned_cli, identity, tmp_path, monkeypatch, capsys, defect
):
    version, entry = versioned_cli
    path = saved_session(tmp_path, version, identity, monkeypatch)
    payloads = {
        "validation": [{"evidence": PRIVATE_VALUE}],
        "missing-field": [{"evidence": [{"content": PRIVATE_VALUE}]}],
        "wrong-shape": {PRIVATE_VALUE: "not-an-input-list"},
        "missing-index": [],
    }
    if defect == "invalid-json":
        path.parent.mkdir()
        path.write_text('{"' + PRIVATE_VALUE, encoding="utf-8")
    else:
        core.dump(path, payloads[defect])
    originals = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    monkeypatch.setattr(sys, "argv", [version, "preflight", "--session", str(tmp_path)])

    with pytest.raises(SystemExit) as exited:
        entry.main()

    captured = capsys.readouterr()
    assert exited.value.code == 2
    expected = (
        f"{version}_validation_failed"
        if defect in {"validation", "missing-field"}
        else "invalid_json"
        if defect == "invalid-json"
        else f"{version}_invalid_inputs_or_local_configuration"
    )
    assert json.loads(captured.out) == {"error": expected}
    assert captured.err == ""
    assert PRIVATE_VALUE not in captured.out + captured.err
    assert str(tmp_path) not in captured.out + captured.err
    assert "Traceback" not in captured.out + captured.err
    assert {p: p.read_bytes() for p in tmp_path.rglob("*.json")} == originals
    assert not (tmp_path / "preflight.json").exists()
    assert not (tmp_path / "preflight").exists()


def test_actual_v18_subprocess_does_not_print_rejected_evidence(identity, tmp_path, monkeypatch):
    path = saved_session(tmp_path, "v18", identity, monkeypatch)
    core.dump(path, [{"evidence": PRIVATE_VALUE}])

    result = subprocess.run(
        [sys.executable, "-m", "powercontext_eval_evidence_admission.v18", "preflight", "--session", str(tmp_path)],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 2
    assert json.loads(result.stdout) == {"error": "v18_validation_failed"}
    assert result.stderr == ""
    assert PRIVATE_VALUE not in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    assert not (tmp_path / "preflight.json").exists()


@pytest.mark.parametrize("failure", ["filesystem", "structure", "unexpected"])
def test_private_exception_values_never_enter_cli_diagnostics(versioned_cli, monkeypatch, capsys, failure):
    version, entry = versioned_cli
    errors = {
        "filesystem": FileNotFoundError(2, PRIVATE_VALUE, PRIVATE_PATH),
        "structure": KeyError(PRIVATE_VALUE),
        "unexpected": RuntimeError(PRIVATE_VALUE + PRIVATE_PATH),
    }

    async def fail(_args):
        raise errors[failure]

    monkeypatch.setattr(entry, "execute", fail)
    monkeypatch.setattr(sys, "argv", [version, "freeze"])
    with pytest.raises(SystemExit) as exited:
        entry.main()
    captured = capsys.readouterr()
    category = "unexpected_evaluation_failure" if failure == "unexpected" else "invalid_inputs_or_local_configuration"
    assert exited.value.code == 2
    assert json.loads(captured.out) == {"error": f"{version}_{category}"}
    assert captured.err == ""
    assert PRIVATE_VALUE not in captured.out + captured.err
    assert PRIVATE_PATH not in captured.out + captured.err


@pytest.mark.parametrize("cancelled", [False, True], ids=["controlled-error", "cancelled"])
def test_controlled_errors_and_cancellation_keep_their_cli_contract(versioned_cli, monkeypatch, capsys, cancelled):
    version, entry = versioned_cli

    async def stop(_args):
        if cancelled:
            raise KeyboardInterrupt
        raise EvaluationError("synthetic_controlled_failure")

    monkeypatch.setattr(entry, "execute", stop)
    monkeypatch.setattr(sys, "argv", [version, "freeze"])
    with pytest.raises(SystemExit) as exited:
        entry.main()
    captured = capsys.readouterr()
    assert exited.value.code == (130 if cancelled else 2)
    assert json.loads(captured.out) == (
        {"state": "cancelled", "automatic_resend": False} if cancelled else {"error": "synthetic_controlled_failure"}
    )
    assert captured.err == ""
