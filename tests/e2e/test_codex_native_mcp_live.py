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

"""Opt-in Windows acceptance test for native Codex MCP tool discovery."""

from __future__ import annotations

import sys

import pytest
from powercontext_integrations.codex import run_codex_diagnostics

from powercontext.cli.system import DiagnosticStatus

pytestmark = [pytest.mark.real_e2e, pytest.mark.skipif(sys.platform != "win32", reason="Windows Codex Desktop only")]


def test_codex_native_mcp_discovers_tools_without_process_authorization(
    pytestconfig: pytest.Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise the same inherited user environment required by Codex Desktop."""

    if not pytestconfig.getoption("run_real_e2e"):
        pytest.skip("requires --run-real-e2e with configured Codex and a running PowerContext Server")
    monkeypatch.delenv("POWERCONTEXT_CODEX_AUTHORIZATION", raising=False)

    diagnostics = run_codex_diagnostics()

    assert diagnostics["codex"].status is DiagnosticStatus.OK
    assert diagnostics["plugin"].status is DiagnosticStatus.OK
    assert diagnostics["mcp_configuration"].status is DiagnosticStatus.OK
    assert diagnostics["authorization"].status is DiagnosticStatus.OK
    assert "Windows user authorization matches" in diagnostics["authorization"].detail
    assert diagnostics["mcp_tools"].status is DiagnosticStatus.OK
    assert "Codex native MCP initialized and discovered" in diagnostics["mcp_tools"].detail
