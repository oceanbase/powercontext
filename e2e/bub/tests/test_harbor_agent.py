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

import shlex
from importlib.metadata import version
from pathlib import Path

from powercontext_e2e.harbor_agent import REMOTE_SOURCE_OVERRIDE, _install_bub_command

_SOURCE_OVERRIDE = Path(__file__).resolve().parents[1] / "source-overrides.txt"


def test_install_bub_command_provides_powercontext_version() -> None:
    version_assignment = f"SETUPTOOLS_SCM_PRETEND_VERSION={shlex.quote(version('powercontext'))}"

    assert version_assignment in _install_bub_command().split()


def test_install_bub_command_overrides_release_floor_for_mounted_source() -> None:
    command = shlex.split(_install_bub_command())
    override_index = command.index("--overrides")

    assert command[override_index + 1] == REMOTE_SOURCE_OVERRIDE
    assert _SOURCE_OVERRIDE.read_text(encoding="utf-8").splitlines()[-1] == (
        "powercontext[client] @ file:///opt/powercontext/source"
    )
