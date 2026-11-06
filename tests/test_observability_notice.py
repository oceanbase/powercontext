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

import re
import shlex
from pathlib import Path

import pytest
from typer.testing import CliRunner

from powercontext.server.observability_notice import write_observability_notice


def test_notice_lists_the_opt_in_commands(capsys: pytest.CaptureFixture[str]) -> None:
    write_observability_notice(enabled=False, server_env="/srv/powercontext/server.env", host="127.0.0.1", port=8100)

    output = capsys.readouterr().out
    assert "Observability is opt-in" in output
    assert "powercontext observability up" in output
    assert (
        "powercontext server run --env-file /srv/powercontext/server.env --host 127.0.0.1 --port 8100 --observability"
    ) in output
    assert "powercontext observability check --exercise" in output


def test_notice_explains_every_restart_option(capsys: pytest.CaptureFixture[str]) -> None:
    write_observability_notice(
        enabled=False,
        server_env="/srv/powercontext/server.env",
        host="0.0.0.0",  # noqa: S104 - verifying how a non-loopback bind is echoed.
        port=9000,
    )

    output = capsys.readouterr().out
    plain = _strip_ansi(output)
    assert "--env-file       Server settings file keep the one used by this run" in plain
    assert "--host           listen address       keep the bind used by this run" in plain
    assert "--port           listen port          keep the port used by this run" in plain
    assert "--observability  telemetry profile    adds only tracing, logging and metrics settings" in plain


def test_notice_names_the_defaults_when_no_bind_or_env_file_was_used(capsys: pytest.CaptureFixture[str]) -> None:
    write_observability_notice(enabled=False)

    output = capsys.readouterr().out
    assert "--no-env-file --host 127.0.0.1 --port 8000 --observability" in output


def test_notice_highlights_headings_and_commands(capsys: pytest.CaptureFixture[str]) -> None:
    write_observability_notice(enabled=False, server_env="/srv/powercontext/server.env", host="127.0.0.1", port=8000)

    output = capsys.readouterr().out
    assert "\x1b[36m\x1b[1m\nObservability is opt-in\x1b[0m" in output
    assert "\x1b[32m\x1b[1m    powercontext observability up " in output


def test_notice_is_silent_when_observability_is_enabled(capsys: pytest.CaptureFixture[str]) -> None:
    write_observability_notice(enabled=True)

    assert capsys.readouterr().out == ""


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


@pytest.mark.parametrize("role", ["api", "background"])
def test_restart_command_keeps_the_current_process_role(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, role: str
) -> None:
    from powercontext.server.cli import app

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("POWERCONTEXT_SERVER_HTTP_PORT=12345\n")
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", "oceanbase")
    monkeypatch.setenv(
        "POWERCONTEXT_SERVER_DATABASE_URL", "mysql+aoceanbase://test@127.0.0.1:2881/notice?charset=utf8mb4"
    )
    monkeypatch.setattr("powercontext.server.cli._run_configured_server", lambda settings: None)
    result = CliRunner().invoke(app, ["run", "--no-env-file", "--role", role])
    assert result.exit_code == 0, result.output
    lines = _strip_ansi(result.output).splitlines()
    command = shlex.split(next(line.strip() for line in lines if line.strip().startswith("powercontext server run")))
    assert command[command.index("--role") + 1] == role
    assert "--no-env-file" in command
    assert command[command.index("--port") + 1] == "8000"
