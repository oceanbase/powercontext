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

"""Repository-wide pytest collection controls."""

from pathlib import Path

import pytest

_REAL_E2E_ROOT = Path(__file__).parent / "e2e" / "real_experience_skill"


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("powercontext-real-e2e")
    group.addoption(
        "--run-real-e2e",
        action="store_true",
        dest="run_real_e2e",
        help="Collect tests that use real Codex, model providers, and configured databases.",
    )
    group.addoption(
        "--real-e2e-mode",
        choices=("baseline", "configured", "all"),
        default="all",
        help="Select the real Experience/Skill journey to run.",
    )
    group.addoption(
        "--real-codex-timeout",
        type=int,
        default=600,
        help="Per-session timeout used by real Codex acceptance tests.",
    )
    group.addoption(
        "--real-e2e-env-file",
        type=Path,
        default=Path(".env"),
        help="Environment file used by configured real-service acceptance tests.",
    )


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    if config.getoption("run_real_e2e"):
        return None
    try:
        collection_path.resolve().relative_to(_REAL_E2E_ROOT.resolve())
    except ValueError:
        return None
    return True
