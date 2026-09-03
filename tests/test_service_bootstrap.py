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

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import powercontext_service_bootstrap.__main__ as service_bootstrap
from powercontext.service import launcher as service_launcher


def _arguments(tmp_path: Path) -> tuple[list[str], Path, Path]:
    state = tmp_path / "state" / "launchd-retry-state.json"
    token = tmp_path / "state" / "launchd-retry.enabled"
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text("enabled\n", encoding="utf-8")
    return (
        [
            "--retry-state",
            str(state),
            "--retry-token",
            str(token),
            "--retry-limit",
            "3",
            "--retry-window-seconds",
            "60",
            "--",
            "--endpoint",
            "http://127.0.0.1:8000",
            "--data-dir",
            str(tmp_path / "data"),
        ],
        state,
        token,
    )


def test_dependency_import_failure_consumes_attempt_before_application_import(tmp_path: Path) -> None:
    isolated = tmp_path / "isolated"
    source = Path(__file__).parents[1] / "src" / "powercontext_service_bootstrap"
    shutil.copytree(source, isolated / "powercontext_service_bootstrap")
    arguments, state, token = _arguments(tmp_path)
    environment = {**os.environ, "PYTHONPATH": str(isolated)}
    command = [sys.executable, "-S", "-m", "powercontext_service_bootstrap", *arguments]

    results = [
        subprocess.run(
            command,
            cwd=isolated,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        for _ in range(3)
    ]

    assert [result.returncode for result in results] == [1, 1, 0]
    assert "No module named 'powercontext'" in results[0].stderr
    assert len(json.loads(state.read_text(encoding="utf-8"))["attempts"]) == 3
    assert not token.exists()


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        '{"version":999,"attempts":[]}',
        '{"version":1,"attempts":[true]}',
        '{"version":1,"attempts":[NaN]}',
    ],
)
def test_retry_state_error_exits_nonzero_and_removes_token(tmp_path: Path, payload: str) -> None:
    arguments, state, token = _arguments(tmp_path)
    state.write_text(payload, encoding="utf-8")
    state.chmod(0o600)

    assert service_bootstrap.main(arguments) == 1
    assert not token.exists()


def test_retry_state_write_error_exits_nonzero_and_removes_token(tmp_path: Path) -> None:
    arguments, _state, token = _arguments(tmp_path)
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("blocked", encoding="utf-8")
    arguments[arguments.index("--retry-state") + 1] = str(blocker / "state.json")

    assert service_bootstrap.main(arguments) == 1
    assert not token.exists()


@pytest.mark.parametrize(
    "unsafe_state",
    [
        "group-readable",
        pytest.param(
            "symlink",
            marks=pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="O_NOFOLLOW is a POSIX service boundary"),
        ),
    ],
)
def test_unsafe_retry_state_exits_nonzero_and_removes_token(tmp_path: Path, unsafe_state: str) -> None:
    arguments, state, token = _arguments(tmp_path)
    state.parent.mkdir(parents=True, exist_ok=True)
    if unsafe_state == "group-readable":
        state.write_text('{"version":1,"attempts":[]}', encoding="utf-8")
        state.chmod(0o640)
    else:
        target = tmp_path / "retry-target.json"
        target.write_text('{"version":1,"attempts":[]}', encoding="utf-8")
        target.chmod(0o600)
        state.symlink_to(target)

    assert service_bootstrap.main(arguments) == 1
    assert not token.exists()


def test_successful_or_already_live_exit_clears_retry_state_and_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments, state, token = _arguments(tmp_path)
    monkeypatch.setattr(service_launcher, "main", lambda _arguments: 0)

    assert service_bootstrap.main(arguments) == 0
    assert not state.exists()
    assert not token.exists()


def test_invalid_retry_configuration_exits_nonzero_and_removes_token(tmp_path: Path) -> None:
    arguments, _state, token = _arguments(tmp_path)
    arguments[arguments.index("--retry-limit") + 1] = "0"

    assert service_bootstrap.main(arguments) == 1
    assert not token.exists()


def test_process_argument_error_removes_the_declared_retry_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arguments, _state, token = _arguments(tmp_path)
    arguments[arguments.index("--retry-limit") + 1] = "not-an-integer"
    monkeypatch.setattr(sys, "argv", ["powercontext_service_bootstrap", *arguments])

    assert service_bootstrap.main() == 2
    assert not token.exists()
