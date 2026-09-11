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

"""Exercise network questions through terminal input without starting a service."""

from __future__ import annotations

import pytest
import typer
from typer.testing import CliRunner

from powercontext.cli.config_wizard import CLIENT, SERVER, Wizard, _network, _scenario
from powercontext.cli.config_wizard_ui import WizardUI


def _run_network(state: Wizard, input_text: str):
    app = typer.Typer()

    @app.command()
    def configure() -> None:
        _network(state)

    return CliRunner().invoke(app, [], input=input_text)


def test_scenario_only_asks_local_or_other_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    state = Wizard(WizardUI("en"), {}, {})
    choices: list[tuple[str, ...]] = []

    def choose(en: str, zh: str, items, default: str) -> str:
        choices.append(tuple(item[0] for item in items))
        return "local"

    monkeypatch.setattr(state.ui, "choose", choose)

    _scenario(state)

    assert choices == [("local", "remote")]


@pytest.mark.parametrize("original_port", ["not-a-number", "0", "65536"])
def test_invalid_existing_port_can_be_repaired_in_the_wizard(original_port: str) -> None:
    values = {SERVER + "HTTP_PORT": original_port}
    state = Wizard(WizardUI("en"), dict(values), dict(values))

    result = _run_network(state, "n\n9000\n")

    assert result.exit_code == 0, result.output
    assert "invalid" in result.output
    assert "Server port [8000]" in result.output
    assert state.values[SERVER + "HTTP_PORT"] == "9000"
    assert state.client[CLIENT + "SERVER_URL"] == "http://127.0.0.1:9000"


def test_invalid_existing_port_retry_is_localized_and_can_accept_fallback() -> None:
    state = Wizard(WizardUI("zh"), {}, {SERVER + "HTTP_PORT": "invalid"})

    result = _run_network(state, "否\n\n")

    assert result.exit_code == 0, result.output
    assert "无效" in result.output
    assert "invalid" not in result.output
    assert state.values[SERVER + "HTTP_PORT"] == "8000"


def test_existing_non_default_local_port_can_be_changed() -> None:
    values = {SERVER + "HTTP_PORT": "9000"}
    state = Wizard(WizardUI("en"), dict(values), dict(values))

    result = _run_network(state, "n\n9100\n")

    assert result.exit_code == 0, result.output
    assert "Server port [9000]" in result.output
    assert state.client[CLIENT + "SERVER_URL"] == "http://127.0.0.1:9100"


def test_custom_remote_url_prompt_has_no_invalid_protocol_only_default() -> None:
    state = Wizard(WizardUI("zh"), {}, {}, scenario="remote")

    result = _run_network(state, "否\ncustom\n0.0.0.0\n8000\n\nhttps://memory.example.com\n")

    assert result.exit_code == 0, result.output
    assert "[https://]" not in result.output
    assert "例如 https://memory.example.com" in result.output
    assert state.client[CLIENT + "SERVER_URL"] == "https://memory.example.com"


def test_reverse_proxy_keeps_loopback_listener_and_uses_public_https_url() -> None:
    state = Wizard(WizardUI("en"), {}, {}, scenario="remote")

    result = _run_network(state, "n\nhttps\n\nhttps://memory.example.com\n")

    assert result.exit_code == 0, result.output
    assert state.values[SERVER + "HTTP_HOST"] == "127.0.0.1"
    assert state.client[CLIENT + "SERVER_URL"] == "https://memory.example.com"
    assert "Nginx" in result.output or "Caddy" in result.output


def test_custom_access_asks_for_listener_and_client_url() -> None:
    state = Wizard(WizardUI("en"), {}, {}, scenario="remote")

    result = _run_network(state, "n\ncustom\n0.0.0.0\n9000\nhttps://memory.example.com\n")

    assert result.exit_code == 0, result.output
    assert state.values[SERVER + "HTTP_HOST"] == "0.0.0.0"  # noqa: S104 - deliberate remote-listener fixture
    assert state.values[SERVER + "HTTP_PORT"] == "9000"
    assert state.client[CLIENT + "SERVER_URL"] == "https://memory.example.com"


def test_default_local_port_keeps_the_short_flow() -> None:
    state = Wizard(WizardUI("en"), {}, {})

    result = _run_network(state, "n\n")

    assert result.exit_code == 0, result.output
    assert "Server port" not in result.output
    assert state.values[SERVER + "HTTP_PORT"] == "8000"
    assert state.forwarded_address == ""


def test_dashboard_question_explains_authentication_and_keeps_mcp_enabled() -> None:
    state = Wizard(WizardUI("en"), {}, {})

    result = _run_network(state, "n\n")

    assert result.exit_code == 0, result.output
    assert "authenticated access" in result.output
    assert "Server token" in result.output
    assert state.values[SERVER + "DASHBOARD_ENABLED"] == "false"
    assert state.values[SERVER + "MCP_ENABLED"] == "true"


def test_ssh_preserves_server_address_and_exposes_forwarded_client_address() -> None:
    state = Wizard(WizardUI("en"), {}, {}, scenario="remote")

    result = _run_network(state, "y\nssh\nt1\n18000\n")

    assert result.exit_code == 0, result.output
    assert state.values[SERVER + "HTTP_HOST"] == "127.0.0.1"
    assert state.values[SERVER + "HTTP_PORT"] == "8000"
    assert state.client[CLIENT + "SERVER_URL"] == "http://127.0.0.1:8000"
    assert state.forwarded_address == "http://127.0.0.1:18000"
    assert "ssh -N -L 18000:127.0.0.1:8000 t1" in result.output
    assert "run the generated command on the client" in result.output


def test_switching_from_ssh_to_local_clears_the_old_forwarded_address() -> None:
    state = Wizard(WizardUI("en"), {}, {}, scenario="remote")
    assert _run_network(state, "n\nssh\nt1\n18000\n").exit_code == 0
    state.scenario = "local"

    result = _run_network(state, "n\n")

    assert result.exit_code == 0, result.output
    assert state.forwarded_address == ""


def test_invalid_ssh_server_port_is_correctable_before_generating_instructions() -> None:
    state = Wizard(WizardUI("en"), {}, {SERVER + "HTTP_PORT": "invalid"}, scenario="remote")

    result = _run_network(state, "n\nssh\n9000\nt1\n18000\n")

    assert result.exit_code == 0, result.output
    assert state.client[CLIENT + "SERVER_URL"] == "http://127.0.0.1:9000"
    assert "ssh -N -L 18000:127.0.0.1:9000 t1" in result.output
