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

"""Command-level contract for the documented ``server run`` invocations.

``check-docs`` only builds the Markdown, so a copy-pasteable command that the bind policy now
rejects (e.g. ``server run --host 0.0.0.0`` with neither authentication nor the opt-in) would ship
green. This test extracts every ``powercontext server run`` command from the remote-access guides --
their inline ``POWERCONTEXT_SERVER_*`` env and their ``--host`` / ``--port`` -- and drives each one
through the real CLI, so a documented command that fails to start fails here instead of only for a
reader who runs it.
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from powercontext.cli.app import create_cli
from powercontext.server.cli import app as server_app

_DOCS_ROOT = Path(__file__).resolve().parent.parent / "docs"
_REMOTE_ACCESS_DOCS = (
    _DOCS_ROOT / "en" / "development" / "remote-access-implementation.md",
    _DOCS_ROOT / "zh" / "development" / "remote-access-implementation.md",
)
_BASH_BLOCK = re.compile(r"```bash\n(.*?)```", re.DOTALL)
_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


class _DocumentedServerRun:
    """A single documented ``server run`` command: its inline env and its CLI arguments."""

    def __init__(self, *, doc: Path, env: dict[str, str], args: list[str], host: str, port: str | None) -> None:
        self.doc = doc
        self.env = env
        self.args = args
        self.host = host
        self.port = port

    def __repr__(self) -> str:  # Readable pytest ids.
        return f"{self.doc.parent.parent.name}:{self.host}:{self.port or 'default'}"


def _parse_server_run_commands(doc: Path) -> list[_DocumentedServerRun]:
    commands: list[_DocumentedServerRun] = []
    for block in _BASH_BLOCK.findall(doc.read_text(encoding="utf-8")):
        # Rejoin shell line-continuations so an env-prefixed command spread over several lines is one
        # logical command, then read each command line.
        for line in block.replace("\\\n", " ").splitlines():
            if "powercontext server run" not in line:
                continue
            tokens = shlex.split(line, comments=True)
            env: dict[str, str] = {}
            index = 0
            while index < len(tokens) and _ENV_ASSIGNMENT.match(tokens[index]):
                key, _, value = tokens[index].partition("=")
                env[key] = value
                index += 1
            rest = tokens[index:]
            run_at = _index_of_server_run(rest)
            args = rest[run_at + 2 :]
            host = _option_value(args, "--host") or "127.0.0.1"
            port = _option_value(args, "--port")
            commands.append(_DocumentedServerRun(doc=doc, env=env, args=["server", "run", *args], host=host, port=port))
    return commands


def _index_of_server_run(tokens: list[str]) -> int:
    for i in range(len(tokens) - 1):
        if tokens[i] == "server" and tokens[i + 1] == "run":
            return i
    raise AssertionError(f"expected a 'server run' command in {tokens!r}")  # noqa: TRY003


def _option_value(args: list[str], name: str) -> str | None:
    for i, token in enumerate(args):
        if token == name and i + 1 < len(args):
            return args[i + 1]
    return None


_DOCUMENTED_COMMANDS = [command for doc in _REMOTE_ACCESS_DOCS for command in _parse_server_run_commands(doc)]


def test_docs_expose_a_non_loopback_bind_command() -> None:
    # Guard the test's premise: if the guides stop documenting a routable bind, the policy coverage
    # below silently evaporates.
    assert any(command.host == "0.0.0.0" for command in _DOCUMENTED_COMMANDS)  # noqa: S104 - documented bind.


@pytest.mark.parametrize("command", _DOCUMENTED_COMMANDS, ids=repr)
def test_documented_server_run_command_starts(command: _DocumentedServerRun, monkeypatch: pytest.MonkeyPatch) -> None:
    run_server = Mock()
    monkeypatch.setattr("powercontext.server.cli._run_server", run_server)
    monkeypatch.setattr("powercontext.server.cli.configure_server_logging", lambda _config: None)
    monkeypatch.setattr("powercontext.server.cli.configure_server_tracing", lambda _config: Mock())
    # Reproduce only the env the doc block sets, with no ambient POWERCONTEXT_SERVER_* leaking in.
    for name in list(os.environ):
        if name.startswith("POWERCONTEXT_SERVER_"):
            monkeypatch.delenv(name, raising=False)
    for name, value in command.env.items():
        monkeypatch.setenv(name, value)

    result = CliRunner().invoke(create_cli([server_app]), command.args)

    assert result.exit_code == 0, result.output
    run_server.assert_called_once()
    assert run_server.call_args.kwargs["host"] == command.host
