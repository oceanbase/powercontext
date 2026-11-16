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

"""Exercise model selection and prompt dispatch with Harbor's uploaded runner and pinned SDK."""

# Fixed local test programs and temporary paths; no user-supplied command is executed.
# ruff: noqa: S603, S607

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from harbor.agents.installed import acp as harbor_acp

from powercontext_e2e.harbor_agent import HARBOR_ACP_SDK_REQUIREMENT


def test_pinned_runner_selects_configured_model_before_prompt(tmp_path: Path) -> None:
    runner = Path(harbor_acp.__file__).with_name("acp_runner.py")
    # Harbor uploads this script away from its own acp.py module; keep the same import resolution.
    uploaded_runner = tmp_path / "acp_runner.py"
    shutil.copy2(runner, uploaded_runner)
    agent = Path(__file__).parent / "fixtures" / "acp_model_agent.py"
    environment = tmp_path / "runner-venv"
    python = environment / "bin" / "python"
    subprocess.run(["uv", "venv", str(environment)], check=True, capture_output=True, text=True, timeout=60)
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), HARBOR_ACP_SDK_REQUIREMENT],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    launcher = tmp_path / "launch-agent.sh"
    launcher.write_text(f"#!/bin/sh\nexec {shlex.quote(str(python))} {shlex.quote(str(agent))}\n", encoding="utf-8")
    launcher.chmod(0o755)
    logs = tmp_path / "logs"
    result = tmp_path / "selected-model.json"
    run = subprocess.run(
        [
            str(python),
            str(uploaded_runner),
            "--instruction",
            "model smoke",
            "--logs-dir",
            str(logs),
            "--launcher",
            str(launcher),
        ],
        cwd=tmp_path,
        env={
            **os.environ,
            "HARBOR_ACP_REQUESTED_MODEL": "fixture-model",
            "POWERCONTEXT_ACP_FIXTURE_RESULT": str(result),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert run.returncode == 0, (
        run.stderr,
        (logs / "acp-summary.json").read_text(encoding="utf-8")
        if (logs / "acp-summary.json").exists()
        else "no summary",
    )
    summary = json.loads((logs / "acp-summary.json").read_text(encoding="utf-8"))
    assert summary["resolved_session_model_id"] == "fixture-model"
    assert summary["prompt_response"]["stopReason"] == "end_turn"
    assert json.loads(result.read_text(encoding="utf-8")) == {
        "selected_model": "fixture-model",
        "prompt": ["model smoke"],
    }
