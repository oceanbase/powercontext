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

"""Independent v6 identity without changing the historical v5 freeze."""

from pathlib import Path
from typing import Any

from ..manifests import EvaluationError, file_hash, read_json
from ..models import digest
from .fidelity import INSTRUCTIONS, TemplateOutput

ROOT = Path(__file__).resolve().parent
CONTRACT_SHA256 = "aa00fb19b06cb593cd1fd733969b055ff6bfc71b5f66b00fb099e60af67dea6f"


def protocol() -> dict[str, Any]:
    if file_hash(ROOT / "contract.json") != CONTRACT_SHA256:
        raise EvaluationError("v6_contract_freeze_mismatch")
    contract = read_json(ROOT / "contract.json")
    if contract["schema"] != "evidence-admission.v6.contract.v1" or contract["live_requests_this_phase"] != 0:
        raise EvaluationError("invalid_v6_offline_contract")
    if file_hash(ROOT / "recorded-atom-reviews.json") != contract["atom_reviews_sha256"]:
        raise EvaluationError("v6_atom_review_freeze_mismatch")
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
