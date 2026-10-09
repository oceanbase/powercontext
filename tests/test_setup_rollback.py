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

from pathlib import Path

import pytest

from powercontext.cli import system, workbuddy


@pytest.mark.parametrize("host", ["claude-code", "workbuddy"])
def test_setup_rollback_restores_exact_configuration_bytes(tmp_path: Path, monkeypatch, host: str) -> None:
    original = b'{\r\n  "custom": true,\n  "note": "keep original line endings"\r\n}\r\n'
    settings = tmp_path / "settings.json"
    settings.write_bytes(b"{}\n")
    if host == "claude-code":
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        system._restore_claude_settings(original)
    else:
        workbuddy._restore_file(settings, original)
    assert settings.read_bytes() == original
