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

"""Startup guidance shared by configuration output and saved instructions."""

import shlex
import sys
from pathlib import Path

from powercontext.transport import is_loopback_host


def personal_service_recommended(host: str) -> bool:
    return sys.platform in {"darwin", "linux"} and is_loopback_host(host)


def server_startup_commands(path: Path, *, host: str = "127.0.0.1") -> list[str]:
    quoted = shlex.quote(str(path))
    commands = [f"powercontext config validate --env-file {quoted}"]
    if personal_service_recommended(host):
        commands.extend((
            f"powercontext service install --env-file {quoted}",
            "powercontext service status",
            f"powercontext doctor --env-file {quoted}",
        ))
    else:
        commands.append(f"powercontext server run --env-file {quoted}")
    return commands
