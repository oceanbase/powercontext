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

"""Process revision identity used to fence mixed Web/Worker deployments."""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path
from re import fullmatch

RUNTIME_SCHEMA_VERSION = 2
_REVISION_ENVIRONMENT = "POWERCONTEXT_EVAL_BUILD_REVISION"


@lru_cache(maxsize=1)
def current_build_revision() -> str:
    """Return a fixed safe revision, preferring an explicit deployment value."""

    configured = os.environ.get(_REVISION_ENVIRONMENT)
    if configured is not None and fullmatch(r"[0-9a-f]{40}", configured) is not None:
        return configured
    try:
        result = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=Path.cwd(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    revision = result.stdout.strip()
    return revision if result.returncode == 0 and fullmatch(r"[0-9a-f]{40}", revision) is not None else "unknown"
