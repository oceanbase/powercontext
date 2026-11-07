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

"""Freeze the separate adapter without modifying v9's accepted offline identity."""

from importlib.metadata import version
from pathlib import Path

from ..manifests import EvaluationError, file_hash, read_json
from ..v9.contract import identity as v9_identity

ROOT = Path(__file__).resolve().parent
CONTRACT_SHA256 = "2a535c7fa06460a78f32687db6b2aea61501ab6ad40eff1231896792c8323014"
PRICING_SHA256 = "047fe1018dd866c80362ad331cc96b7d927f6b93dfa0d0a81ab2fc91ea2125b5"


def identity():
    if file_hash(ROOT / "contract.json") != CONTRACT_SHA256 or file_hash(ROOT / "pricing.json") != PRICING_SHA256:
        raise EvaluationError("sdk_preflight_contract_changed")
    return {
        "contract": read_json(ROOT / "contract.json"),
        "pricing": read_json(ROOT / "pricing.json"),
        "v9_dependency": v9_identity(),
        "sdk_versions": {p: version(p) for p in ("openai", "httpx", "pydantic-ai-slim")},
        "files": {
            str(p.relative_to(ROOT)): file_hash(p) for p in sorted(ROOT.rglob("*")) if p.suffix in {".py", ".json"}
        },
        "live_authorized": False,
    }
