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

"""Independent v9 freeze including every inherited v8/v7 evaluator dependency."""

from pathlib import Path

from ..manifests import EvaluationError, file_hash, read_json
from ..models import digest
from ..v8.contract import identity as v8_identity
from .codec import BASE_INSTRUCTIONS, INSTRUCTIONS, TemplateOutput

ROOT = Path(__file__).resolve().parent
CONTRACT_SHA256 = "997edf020be9412cc8767dfe820fcf703454ea476810b50fa9178afa5d3e7c3a"


def identity():
    if file_hash(ROOT / "contract.json") != CONTRACT_SHA256:
        raise EvaluationError("v9_contract_freeze_mismatch")
    return {
        "contract": read_json(ROOT / "contract.json"),
        "v8_dependency": v8_identity(),
        "files": {
            str(p.relative_to(ROOT)): file_hash(p) for p in sorted(ROOT.rglob("*")) if p.suffix in {".py", ".json"}
        },
        "production_rules_hash": digest(BASE_INSTRUCTIONS),
        "instructions_hash": digest(INSTRUCTIONS),
        "schema_hash": digest(TemplateOutput.model_json_schema()),
        "production_authorized": False,
    }
