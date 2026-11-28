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

"""Identity of the execution boundary, separate from all accepted offline protocols."""

from pathlib import Path

from ..manifests import file_hash, read_json
from ..v9_preflight.contract import identity as adapter_identity

ROOT = Path(__file__).resolve().parent


def contract():
    return read_json(ROOT / "contract.json")


def identity():
    return {
        "contract": contract(),
        "adapter_dependency": adapter_identity(),
        "files": {
            str(p.relative_to(ROOT)): file_hash(p) for p in sorted(ROOT.iterdir()) if p.suffix in {".py", ".json"}
        },
    }
