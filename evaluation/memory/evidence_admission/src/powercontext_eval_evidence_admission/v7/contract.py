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

"""Independent v7 identity; does not change the v5/v6 freeze."""

from pathlib import Path
from typing import Any

from ..manifests import EvaluationError, file_hash, load_suite, read_json
from ..models import digest
from ..report import code_identity
from ..v6.contract import identity as v6_identity
from .fidelity import INSTRUCTIONS, TemplateOutput

ROOT = Path(__file__).resolve().parent
CONTRACT_SHA256 = "18da6771fe04bd19693cfe61603c7eb399d7ddfb90a9ffd9eaa95ec6b3d80a48"


def protocol() -> dict[str, Any]:
    if file_hash(ROOT / "contract.json") != CONTRACT_SHA256:
        raise EvaluationError("v7_contract_freeze_mismatch")
    contract = read_json(ROOT / "contract.json")
    if contract["schema"] != "evidence-admission.v7.contract.v1" or contract["live_requests_this_phase"] != 0:
        raise EvaluationError("invalid_v7_offline_contract")
    if contract.get("fact_selection_policy_changed") is not False:
        raise EvaluationError("v7_must_not_change_fact_selection")
    return contract


def identity() -> dict[str, Any]:
    return {
        "contract": protocol(),
        "v6_dependency": v6_identity(),
        "reference_dependency": code_identity(load_suite(experiment_version="v5")),
        "files": {
            str(p.relative_to(ROOT)): file_hash(p) for p in sorted(ROOT.rglob("*")) if p.suffix in {".py", ".json"}
        },
        "instructions_hash": digest(INSTRUCTIONS),
        "schema_hash": digest(TemplateOutput.model_json_schema()),
        "production_authorized": False,
    }
