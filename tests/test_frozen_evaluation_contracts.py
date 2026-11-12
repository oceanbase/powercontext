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

"""Historical contract formatting is an external byte-preservation guarantee."""

from __future__ import annotations

import json
from pathlib import Path

from check_frozen_evaluation_contracts import FROZEN_CONTRACTS, changed_contracts


def test_committed_historical_contract_bytes_are_preserved():
    assert changed_contracts(Path(__file__).resolve().parents[1]) == ()


def test_semantically_identical_reformatting_does_not_preserve_frozen_evidence(tmp_path):
    root = Path(__file__).resolve().parents[1]
    name = next(iter(FROZEN_CONTRACTS))
    target = tmp_path / name
    target.parent.mkdir(parents=True)
    target.write_bytes((root / name).read_bytes())
    assert changed_contracts(tmp_path) == ()
    parsed = json.loads(target.read_text())
    target.write_text(json.dumps(parsed, ensure_ascii=False, indent=2) + "\n")
    assert changed_contracts(tmp_path) == (name,)
