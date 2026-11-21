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

"""Build labels may change; immutable input, code, SDK and lock identities may not."""

from __future__ import annotations

from copy import deepcopy

import pytest

from powercontext_eval_evidence_admission.v12.compatibility import preparation_compatibility


def baseline():
    return {
        "reference_dependency": {
            "suite_code_hash": "suite",
            "runtime_code_hash": "runtime",
            "dependency_hashes": {"uv.lock": "locked"},
            "instruction_hash": "instructions",
            "versions": {
                "python": "3.11.15",
                "sqlite": "3.45",
                "powercontext": "old-build",
                "pydantic-ai-slim": "same-sdk",
            },
        },
        "schema_hash": "schema",
        "files": {"codec.py": "codec"},
    }


@pytest.mark.parametrize("field", ["python", "sqlite", "powercontext"])
def test_new_execution_metadata_is_retained_separately(field):
    recorded = baseline()
    current = deepcopy(recorded)
    current["reference_dependency"]["versions"][field] = "new-execution"
    receipt = preparation_compatibility(recorded, current)
    assert receipt["content_matches"]
    assert receipt["recorded_execution_environment"]["/reference_dependency"][field] != "new-execution"
    assert receipt["current_execution_environment"]["/reference_dependency"][field] == "new-execution"
    assert not receipt["historical_live_execution_reused"]
    assert recorded == baseline()


@pytest.mark.parametrize("field", ["runtime", "suite", "lock", "instructions", "schema", "codec", "sdk"])
def test_content_or_provider_dependencies_cannot_be_ignored(field):
    recorded = baseline()
    current = deepcopy(recorded)
    reference = current["reference_dependency"]
    if field in {"runtime", "suite"}:
        reference[field + "_code_hash"] = "changed"
    elif field == "lock":
        reference["dependency_hashes"]["uv.lock"] = "changed"
    elif field == "instructions":
        reference["instruction_hash"] = "changed"
    elif field == "schema":
        current["schema_hash"] = "changed"
    elif field == "codec":
        current["files"]["codec.py"] = "changed"
    else:
        reference["versions"]["pydantic-ai-slim"] = "changed"
    assert not preparation_compatibility(recorded, current)["content_matches"]


@pytest.mark.parametrize("protocol", ["v12", "v13"])
def test_live_session_keeps_exact_execution_identity_gate(tmp_path, monkeypatch, protocol):
    import asyncio
    import importlib

    from powercontext_eval_evidence_admission.historical_artifacts import historical_results_root
    from powercontext_eval_evidence_admission.manifests import EvaluationError

    session = importlib.import_module(f"powercontext_eval_evidence_admission.{protocol}.session")
    original = session.window_identity

    def changed_environment():
        identity = deepcopy(original())

        def change(value):
            if not isinstance(value, dict):
                return
            if {"suite_code_hash", "runtime_code_hash", "versions"} <= value.keys():
                value["versions"]["python"] = "different-runtime"
            for child in value.values():
                change(child)

        change(identity)
        return identity

    monkeypatch.setattr(session, "window_identity", changed_environment)
    try:
        baseline_dir = historical_results_root() / "v11-offline"
    except EvaluationError as error:
        pytest.skip(str(error))
    with pytest.raises(EvaluationError, match="baseline_not_verified"):
        asyncio.run(session.prepare_session(baseline_dir, tmp_path / "session", mode="live"))
    assert not (tmp_path / "session").exists()
