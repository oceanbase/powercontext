# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import importlib
import json
from pathlib import Path

import pytest

from tests.integrations.test_hermes_provider import hermes_modules as hermes_modules

FIXTURE = json.loads(Path(__file__).with_name("capture_coverage_cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", FIXTURE["declarations"], ids=lambda case: case["id"])
def test_shared_capture_coverage_declarations(hermes_modules, case):
    helpers = importlib.import_module("plugins.powercontext.helpers")
    actual = helpers.capture_coverage(case["projection"], reasons=case["reasons"])
    assert actual == case["expected"]
    assert len(json.dumps(actual, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= 512


@pytest.mark.parametrize("case", FIXTURE["interpretations"], ids=lambda case: case["id"])
def test_shared_capture_coverage_interpretation(hermes_modules, case):
    helpers = importlib.import_module("plugins.powercontext.helpers")
    before = json.dumps(case["metadata"], ensure_ascii=False)
    assert helpers.capture_coverage_status(case["metadata"]) == case["expected"]
    assert json.dumps(case["metadata"], ensure_ascii=False) == before
