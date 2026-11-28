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

"""Independent v14 identity; does not change the v5/v9/v12/v13 freeze."""

from pathlib import Path
from typing import Any

from ..manifests import EvaluationError, file_hash, read_json
from ..models import digest
from .codec import INSTRUCTIONS, TemplateOutput

ROOT = Path(__file__).resolve().parent


def protocol() -> dict[str, Any]:
    contract = read_json(ROOT / "contract.json")
    if contract["schema"] != "evidence-admission.v14.contract.v1" or contract["live_requests_this_phase"] != 0:
        raise EvaluationError("invalid_v14_offline_contract")
    if contract.get("fact_selection_policy_changed") is not False:
        raise EvaluationError("v14_must_not_change_fact_selection")
    if contract.get("baseline_commit") != "64c6fcfb":
        raise EvaluationError("v14_baseline_commit_mismatch")
    return contract


def identity() -> dict[str, Any]:
    return {
        "contract": protocol(),
        "files": {
            str(p.relative_to(ROOT)): file_hash(p) for p in sorted(ROOT.rglob("*")) if p.suffix in {".py", ".json"}
        },
        "instructions_hash": digest(INSTRUCTIONS),
        "schema_hash": digest(TemplateOutput.model_json_schema()),
        "production_authorized": False,
    }
