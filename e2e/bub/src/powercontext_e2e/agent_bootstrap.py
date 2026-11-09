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

"""Prepare the ACP runner without reinstalling an existing Python runtime."""

from __future__ import annotations

import shlex

# Match bub-acp-server's SDK pin and the harness container's uv release.
ACP_SDK_REQUIREMENT = "agent-client-protocol==1.0.0rc2"
UV_VERSION = "0.10.12"
UV_REQUIREMENT = f"uv=={UV_VERSION}"


def runner_setup_command(runner_path: str, fallback: str, *, log_path: str = "/logs/agent/bootstrap.log") -> str:
    """Reuse Python images, retain the upstream bootstrap for other task images, and preserve failure logs."""
    runner = shlex.quote(runner_path)
    python = shlex.quote(f"{runner_path}/bin/python")
    log = shlex.quote(log_path)
    return f"""
set -euo pipefail
mkdir -p "$(dirname {log})"
{{
  if python3 -c 'import ensurepip, ssl, venv' >/dev/null 2>&1; then
    echo 'ACP bootstrap: reuse the task image Python runtime'
    if [ ! -x {python} ]; then
      python3 -m venv {runner}
    fi
  else
    echo 'ACP bootstrap: install the missing system runtime'
    bash -c {shlex.quote(fallback)}
  fi
  if command -v uv >/dev/null 2>&1 && command -v uvx >/dev/null 2>&1 \
      && [ "$(uv --version | cut -d ' ' -f 2)" = {shlex.quote(UV_VERSION)} ]; then
    echo 'ACP bootstrap: reuse the pinned task image uv'
    [ "$(command -v uv)" -ef {runner}/bin/uv ] || cp "$(command -v uv)" {runner}/bin/uv
    [ "$(command -v uvx)" -ef {runner}/bin/uvx ] || cp "$(command -v uvx)" {runner}/bin/uvx
    UV_DEFAULT_INDEX="${{PIP_INDEX_URL:-https://pypi.org/simple}}" {runner}/bin/uv pip install --python {python} \
      {shlex.quote(ACP_SDK_REQUIREMENT)}
  else
    echo 'ACP bootstrap: install pinned SDK and uv'
    {python} -m pip install --disable-pip-version-check --no-input --retries 1 --timeout 30 \
      {shlex.quote(ACP_SDK_REQUIREMENT)} {shlex.quote(UV_REQUIREMENT)}
  fi
  echo 'ACP bootstrap: ready'
}} 2>&1 | tee {log}
""".strip()
