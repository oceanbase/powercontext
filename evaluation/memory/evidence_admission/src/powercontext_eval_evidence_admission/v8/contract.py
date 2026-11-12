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

"""Freeze the v8 contract and every imported evaluator protocol dependency."""

from pathlib import Path

from ..manifests import EvaluationError, file_hash, read_json
from ..v7.contract import identity as v7_identity

ROOT = Path(__file__).resolve().parent
CONTRACT_SHA256 = "97fca2f61f5fac9a8d77d88a946253fe6be435f3a72f03ab919919c295887297"


def identity():
    if file_hash(ROOT / "contract.json") != CONTRACT_SHA256:
        raise EvaluationError("v8_contract_freeze_mismatch")
    return {
        "contract": read_json(ROOT / "contract.json"),
        "v7_dependency": v7_identity(),
        "files": {
            str(p.relative_to(ROOT)): file_hash(p) for p in sorted(ROOT.rglob("*")) if p.suffix in {".py", ".json"}
        },
        "production_authorized": False,
    }
