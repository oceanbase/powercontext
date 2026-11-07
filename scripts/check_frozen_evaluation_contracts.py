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

"""Preserve historical experiment bytes instead of applying today's JSON formatter."""

from __future__ import annotations

import hashlib
from pathlib import Path

FROZEN_CONTRACTS = {
    "evaluation/memory/evidence_admission/src/powercontext_eval_evidence_admission/v14/contract.json": "ea129d7358767d462163f042c20e8d6e81f0a6ab30f01e956709acec1349832b",
}


def changed_contracts(root: Path) -> tuple[str, ...]:
    changed = []
    for name, expected in FROZEN_CONTRACTS.items():
        path = root / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            changed.append(name)
    return tuple(changed)


def main() -> int:
    changed = changed_contracts(Path(__file__).resolve().parents[1])
    for name in changed:
        print(f"Frozen evaluation contract bytes changed: {name}")
    return int(bool(changed))


if __name__ == "__main__":
    raise SystemExit(main())
