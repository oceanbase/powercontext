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


"""Test-process-only native activation; never changes the product default."""

import hashlib
import importlib
import sys
import unicodedata
from pathlib import Path

CRATE = Path(__file__).resolve().parents[1]
calls = 0


def pytest_configure(config):
    if unicodedata.unidata_version != "16.0.0":
        message = "Pilot requires Unicode16"
        raise RuntimeError(message)
    sys.path.insert(0, str(CRATE / "python"))
    pc_lexical_pilot = importlib.import_module("pc_lexical_pilot")

    from powercontext.builtin.artifacts import search

    original = search.analyze_text
    native = pc_lexical_pilot.analyze_text

    def required_native(value):
        global calls
        calls += 1
        return native(value)

    # Some modules may bind the public function before pytest configuration.
    for name, module in list(sys.modules.items()):
        if name.startswith("powercontext.") and getattr(module, "analyze_text", None) is original:
            vars(module)["analyze_text"] = required_native


def pytest_sessionfinish(session, exitstatus):
    native = importlib.import_module("pc_lexical_pilot._native")
    if native.__file__ is None:
        message = "Native module has no build identity"
        raise RuntimeError(message)

    sha = hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest()
    print(f"\nPC_NATIVE: calls={calls} sha256={sha} path={native.__file__}")
    if calls == 0:
        session.exitstatus = 1
