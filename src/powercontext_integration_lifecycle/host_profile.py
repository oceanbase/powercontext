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

"""The values in which one host's lifecycle differs from another's."""

from __future__ import annotations

from dataclasses import dataclass

from .bootstrap_state import BootstrapState
from .diagnostics import DiagnosticThrottle


@dataclass(frozen=True)
class HostProfile:
    """What the lifecycle flows need to know about one host.

    ``integration`` names the host in Server requests, and ``user_agent`` identifies its plugin. ``component`` begins
    the component of each diagnostic the host reports, such as ``powercontext.claude_code``. ``diagnostics`` and
    ``bootstrap_state`` locate the host's diagnostic throttle and its bootstrap receipts.
    """

    integration: str
    user_agent: str
    component: str
    diagnostics: DiagnosticThrottle
    bootstrap_state: BootstrapState
