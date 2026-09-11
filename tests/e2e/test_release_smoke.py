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

"""Exercise release verification against a fresh Server database before publishing."""

import os
from importlib.metadata import version

from ci_release_smoke import run_smoke


def test_release_smoke_creates_its_scope_in_a_fresh_database(monkeypatch) -> None:
    for key in tuple(os.environ):
        if key.startswith("POWERCONTEXT_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")

    run_smoke(version("powercontext"), timeout_seconds=60)
