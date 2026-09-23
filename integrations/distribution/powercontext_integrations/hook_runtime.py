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

"""Check the installed client executable used by native hook hosts."""

from __future__ import annotations

from shutil import which
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from powercontext.cli.system import Diagnostic


def hook_client_diagnostic() -> Diagnostic:
    from powercontext.cli.system import Diagnostic, DiagnosticStatus

    missing = [name for name in ("powercontext-hook", "uvx", "npx") if which(name) is None]
    executable = which("powercontext-hook")
    return Diagnostic(
        status=DiagnosticStatus.FAILED if missing else DiagnosticStatus.OK,
        detail=f"Missing executables: {', '.join(missing)}. Install the PowerContext client, uv, and Node.js."
        if missing
        else str(executable),
    )


def require_hook_client() -> None:
    from powercontext.cli.system import SetupError

    diagnostic = hook_client_diagnostic()
    if not diagnostic.ok:
        raise SetupError(diagnostic.detail)
