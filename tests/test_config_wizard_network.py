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

import socket

import pytest
import typer
from typer.testing import CliRunner

from powercontext.cli.config_wizard import CLIENT, SERVER, Wizard, _network, _scenario
from powercontext.cli.config_wizard_ui import WizardUI


@pytest.fixture(autouse=True)
def available_listener(monkeypatch):
    """Stub routine probes and return the real probe for socket conflict coverage."""
    from powercontext.cli import config_wizard

    probe = config_wizard._listener_port_available
    monkeypatch.setattr(config_wizard, "_listener_port_available", lambda host, port: True)
    return probe


def _run_network(state: Wizard, input_text: str):
    app = typer.Typer()

    @app.command()
    def configure() -> None:
        _network(state)

    return CliRunner().invoke(app, [], input=input_text)


@pytest.mark.parametrize("port", [1, 65535])
def test_local_port_retries_invalid_input_and_accepts_boundaries(port: int) -> None:
    """Reject non-integer and out-of-range input before accepting a valid port."""
    state = Wizard(WizardUI("en"), {}, {})
    result = _run_network(state, f"n\ninvalid\n0\n65536\n{port}\n")
    assert result.exit_code == 0, result.output
    assert "Enter an integer." in result.output
    assert "between 1 and 65535" in result.output
    assert state.values[SERVER + "HTTP_PORT"] == str(port)


def test_ssh_server_port_is_editable_independently_of_forwarded_port() -> None:
    """Use the chosen Server port as the SSH destination, not the client port."""
    state = Wizard(WizardUI("en"), {}, {}, scenario="remote")
    result = _run_network(state, "y\nssh\n19000\nt1\n18000\n")
    assert result.exit_code == 0, result.output
    assert state.values[SERVER + "HTTP_PORT"] == "19000"
    assert "ssh -N -L 18000:127.0.0.1:19000 t1" in result.output
    assert state.forwarded_address == "http://127.0.0.1:18000"


def test_occupied_port_can_be_kept_with_process_shutdown_warning(monkeypatch, available_listener) -> None:
    """Detect a real listener and retain the user's choice with a persistent warning."""
    from powercontext.cli import config_wizard

    monkeypatch.setattr(config_wizard, "_listener_port_available", available_listener)
    state = Wizard(WizardUI("en"), {}, {})
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        result = _run_network(state, f"n\n{port}\nkeep\n")
    assert result.exit_code == 0, result.output
    assert "already in use" in result.output
    assert state.values[SERVER + "HTTP_PORT"] == str(port)
    assert any("stop or terminate the process" in note for note in state.notes)


def test_occupied_port_can_be_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry port selection rather than saving a known occupied listener."""
    from powercontext.cli import config_wizard

    monkeypatch.setattr(config_wizard, "_listener_port_available", lambda host, port: port != 18000)
    state = Wizard(WizardUI("zh"), {}, {})
    result = _run_network(state, "否\n18000\nchange\n19000\n")
    assert result.exit_code == 0, result.output
    assert "已被占用" in result.output
    assert state.values[SERVER + "HTTP_PORT"] == "19000"
    assert state.client[CLIENT + "SERVER_URL"] == "http://127.0.0.1:19000"


def test_probe_failure_warns_without_claiming_port_is_occupied(monkeypatch: pytest.MonkeyPatch) -> None:
    """Distinguish unavailable probe results from a confirmed listener conflict."""
    from powercontext.cli import config_wizard

    def unavailable(host: str, port: int) -> bool:
        """Simulate a bind permission failure without opening a socket."""
        raise PermissionError

    monkeypatch.setattr(config_wizard, "_listener_port_available", unavailable)
    state = Wizard(WizardUI("en"), {}, {})
    result = _run_network(state, "n\n18000\n")
    assert result.exit_code == 0, result.output
    assert "Could not verify local listener" in result.output
    assert "already in use" not in result.output
    assert state.values[SERVER + "HTTP_PORT"] == "18000"


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
    assert "Server port [17429]" in result.output
    assert state.values[SERVER + "HTTP_PORT"] == "9000"
    assert state.client[CLIENT + "SERVER_URL"] == "http://127.0.0.1:9000"


def test_invalid_existing_port_retry_is_localized_and_can_accept_fallback() -> None:
    state = Wizard(WizardUI("zh"), {}, {SERVER + "HTTP_PORT": "invalid"})

    result = _run_network(state, "否\n\n")

    assert result.exit_code == 0, result.output
    assert "无效" in result.output
    assert "invalid" not in result.output
    assert state.values[SERVER + "HTTP_PORT"] == "17429"


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


def test_fresh_local_port_defaults_to_17429() -> None:
    """Allow accepting the default listener port on a fresh setup."""
    state = Wizard(WizardUI("en"), {}, {})

    result = _run_network(state, "n\n\n")

    assert result.exit_code == 0, result.output
    assert "Server port [17429]" in result.output
    assert "Dashboard, HTTP API, and MCP share" in result.output
    assert "restart it with the saved configuration" in result.output
    assert state.values[SERVER + "HTTP_PORT"] == "17429"
    assert state.forwarded_address == ""


def test_fresh_local_setup_defaults_to_no_authentication() -> None:
    state = Wizard(WizardUI("en"), {}, {})

    result = _run_network(state, "\n\n")

    assert result.exit_code == 0, result.output
    assert state.values[SERVER + "DASHBOARD_ENABLED"] == "false"
    assert state.values[SERVER + "ACCESS_MODE"] == "disabled"
    assert SERVER + "AUTH_TOKEN" not in state.values
    assert CLIENT + "API_TOKEN" not in state.client


@pytest.mark.parametrize("dashboard", ["true", "false"])
def test_existing_local_authentication_is_preserved_when_accepting_defaults(dashboard: str) -> None:
    values = {
        SERVER + "DASHBOARD_ENABLED": dashboard,
        SERVER + "ACCESS_MODE": "enforced",
        SERVER + "AUTH_TOKEN": "existing-test-token",
    }
    state = Wizard(WizardUI("en"), dict(values), dict(values))

    result = _run_network(state, "\n\n")

    assert result.exit_code == 0, result.output
    assert state.values[SERVER + "DASHBOARD_ENABLED"] == dashboard
    assert state.values[SERVER + "ACCESS_MODE"] == "enforced"
    assert state.client[CLIENT + "API_TOKEN"] == "existing-test-token"


def test_dashboard_authentication_can_be_enabled() -> None:
    state = Wizard(WizardUI("en"), {}, {})

    result = _run_network(state, "y\n\ny\n")

    assert result.exit_code == 0, result.output
    assert state.values[SERVER + "DASHBOARD_ENABLED"] == "true"
    assert state.values[SERVER + "ACCESS_MODE"] == "enforced"
    assert state.values[SERVER + "AUTH_TOKEN"]
    assert state.client[CLIENT + "API_TOKEN"] == state.values[SERVER + "AUTH_TOKEN"]


def test_ssh_preserves_server_address_and_exposes_forwarded_client_address() -> None:
    state = Wizard(WizardUI("en"), {}, {}, scenario="remote")

    result = _run_network(state, "y\nssh\n\nt1\n18000\n")

    assert result.exit_code == 0, result.output
    assert state.values[SERVER + "HTTP_HOST"] == "127.0.0.1"
    assert state.values[SERVER + "HTTP_PORT"] == "17429"
    assert state.client[CLIENT + "SERVER_URL"] == "http://127.0.0.1:17429"
    assert state.forwarded_address == "http://127.0.0.1:18000"
    assert "ssh -N -L 18000:127.0.0.1:17429 t1" in result.output
    assert "run the generated command on the client" in result.output


def test_switching_from_ssh_to_local_clears_the_old_forwarded_address() -> None:
    state = Wizard(WizardUI("en"), {}, {}, scenario="remote")
    assert _run_network(state, "n\nssh\n\nt1\n18000\n").exit_code == 0
    state.scenario = "local"

    result = _run_network(state, "n\n\n")

    assert result.exit_code == 0, result.output
    assert state.forwarded_address == ""


def test_invalid_ssh_server_port_is_correctable_before_generating_instructions() -> None:
    state = Wizard(WizardUI("en"), {}, {SERVER + "HTTP_PORT": "invalid"}, scenario="remote")

    result = _run_network(state, "n\nssh\n9000\nt1\n18000\n")

    assert result.exit_code == 0, result.output
    assert state.client[CLIENT + "SERVER_URL"] == "http://127.0.0.1:9000"
    assert "ssh -N -L 18000:127.0.0.1:9000 t1" in result.output
