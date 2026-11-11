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

"""Capability simulations; these are not native Windows acceptance tests."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from evaluation.performance.input_encoding import __main__ as cli
from evaluation.performance.input_encoding import identity, judging, runner, semantics
from powercontext.builtin.runtime.config import InferenceConfig


def _unsupported_os(monkeypatch, capability):
    view = dict(vars(os))
    if capability == "posix":
        view["name"] = "nt"
    else:
        view.pop(capability, None)
    # Do not change the shared os module: pathlib and pytest still run on the host.
    monkeypatch.setattr(identity, "os", SimpleNamespace(**view))


@pytest.mark.parametrize("capability", ["posix", "O_NOFOLLOW", "O_NONBLOCK", "getuid"])
def test_private_identity_rejects_unsupported_platform_before_creating_output(tmp_path, monkeypatch, capability):
    _unsupported_os(monkeypatch, capability)
    output = tmp_path / "private-output"
    with pytest.raises(identity.ConfigurationIdentityError, match="requires POSIX"):
        identity.prepare_configuration_identity(output, {"probe": InferenceConfig()}, environment={})
    assert not output.exists()


@pytest.mark.parametrize("command", ["run", "judge", "semantic-probe"])
@pytest.mark.parametrize("capability", ["posix", "O_NOFOLLOW", "getuid"])
def test_live_cli_rejects_unsupported_platform_before_private_reads_or_writes(
    tmp_path, monkeypatch, capsys, command, capability
):
    _unsupported_os(monkeypatch, capability)
    output = tmp_path / "private-output"
    environment = tmp_path / "private-credentials.env"
    environment.write_text("POWERCONTEXT_SERVER_DATABASE_KIND=sqlite\n")
    cases = tmp_path / "cases.json"
    cases.write_text('["fixture-case"]')
    monkeypatch.setattr(runner, "load_catalog", lambda path: SimpleNamespace(require=lambda name: object()))
    arguments = ["input_encoding", command, "--output", str(output), "--env-file", str(environment)]
    if command == "run":
        arguments.extend(["--case-ids", str(cases), "--repeats", "1"])

    def private_settings_forbidden(**kwargs):
        pytest.fail("unsupported platforms must be rejected before reading private settings")

    for module in (runner, judging, semantics):
        monkeypatch.setattr(module, "server_settings_context", private_settings_forbidden)
    monkeypatch.setattr("sys.argv", arguments)
    with pytest.raises(SystemExit) as captured:
        cli.main()
    assert captured.value.code == 2
    error = capsys.readouterr().err
    assert "requires POSIX" in error
    assert "Windows" in error
    assert str(environment) not in error
    assert str(output) not in error
    assert "Traceback" not in error
    assert not output.exists()


def test_semantic_dry_run_remains_available_without_private_identity_support(tmp_path, monkeypatch, capsys):
    _unsupported_os(monkeypatch, "posix")
    output = tmp_path / "public-plan"
    monkeypatch.setattr("sys.argv", ["input_encoding", "semantic-probe", "--dry-run", "--output", str(output)])
    cli.main()
    result = json.loads(capsys.readouterr().out)
    assert result["required_rendering_coverage_complete"]
    assert (output / "plan.json").is_file()
    assert not (output / identity.KEY_FILENAME).exists()


def test_cli_identity_diagnostic_does_not_disclose_private_exception_context(tmp_path, monkeypatch, capsys):
    async def failed_identity(args):
        raise identity.ConfigurationIdentityError(  # noqa: TRY003
            "Configuration identity could not be verified"
        ) from OSError("private-path/provider-credential")

    monkeypatch.setattr(cli, "run", failed_identity)
    monkeypatch.setattr(
        "sys.argv", ["input_encoding", "run", "--output", str(tmp_path / "output"), "--env-file", "private.env"]
    )
    with pytest.raises(SystemExit) as captured:
        cli.main()
    assert captured.value.code == 2
    error = capsys.readouterr().err
    assert "Configuration identity could not be verified" in error
    assert "private-path" not in error
    assert "provider-credential" not in error
    assert "Traceback" not in error
