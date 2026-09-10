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

"""New-user instructions cover remote endpoints, Scope binding, and Profile setup."""

import io
import json
import re
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path

import pytest
from typer.testing import CliRunner

import powercontext.cli.config_wizard as wizard
from powercontext.cli.config import app
from powercontext.cli.config_wizard_ui import WizardUI
from powercontext.cli.env_file import parse_environment


@pytest.mark.parametrize(
    "agents",
    [
        "codex\nother\n\ndefault\nclaude-code\nserver\n\ndefault\n",
        "claude-code\nserver\n\ndefault\ncodex\nother\n\ndefault\n",
    ],
)
def test_mixed_ssh_agents_keep_their_endpoints_and_codex_client_checks(tmp_path, agents) -> None:
    output = tmp_path / "server.env"
    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--output", str(output)],
        input=f"sqlite\n{tmp_path / 'context.db'}\nremote\nbase\ny\nssh\nt1\n18000\n{agents}none\ny\n",
    )

    assert result.exit_code == 0, result.output
    client = parse_environment(output.read_text())
    assert client["POWERCONTEXT_CLIENT_SERVER_URL"] == "http://127.0.0.1:18000"
    assert client["POWERCONTEXT_CLAUDE_SERVER_URL"] == "http://127.0.0.1:8000"
    steps = output.with_name("server.env.next-steps.md").read_text()
    assert "Run each Agent's commands on the computer where that Agent will run" in steps
    assert "POWERCONTEXT_CLIENT_SERVER_URL=http://127.0.0.1:8000 powercontext ready" in steps


def _state() -> wizard.Wizard:
    state = wizard.Wizard(WizardUI("en", interactive=False), {}, {})
    state.client = {"POWERCONTEXT_CLIENT_SERVER_URL": "http://127.0.0.1:8000"}
    state.agents = ("claude-code",)
    state.agent_addresses = {"claude-code": "http://127.0.0.1:8000"}
    state.planned_scopes = {"claude-code": "claude-example"}
    return state


def test_scope_binding_reloads_client_environment_before_agent_installation(tmp_path) -> None:
    state = _state()
    client_file = tmp_path / ".env"

    steps = wizard._next_steps(state, tmp_path / ".env", client_file)

    creation = steps.index("POST http://127.0.0.1:8000/v1/scopes")
    installation = steps.index("powercontext setup claude-code")
    assert f". {client_file}" in steps[creation:installation]
    assert "Reload the edited client environment before starting a new Agent" in steps


def _profile_script() -> str:
    state = _state()
    state.features = {"profile"}
    steps = wizard._next_steps(state, Path("/example/.env"), Path("/example/.env"))
    scripts = re.findall(r"python3 - <<'PY'\n(.*?)\nPY", steps, re.DOTALL)
    assert len(scripts) == 1, "Profile setup must include a runnable standard-library Python command"
    return scripts[0]


@pytest.mark.parametrize("existing_version", [None, 7])
def test_profile_setup_uses_current_version_and_review_required(monkeypatch, existing_version) -> None:
    script = _profile_script()
    monkeypatch.setenv("POWERCONTEXT_CLAUDE_SCOPE_ID", "scope_test")
    monkeypatch.setenv("POWERCONTEXT_CLAUDE_AUTHORIZATION", "Bearer test-token")
    requests = []

    def urlopen(request, **kwargs):
        requests.append(request)
        if request.get_method() == "GET":
            if existing_version is None:
                raise urllib.error.HTTPError(request.full_url, 404, "not found", Message(), None)
            return io.BytesIO(
                json.dumps({
                    "scope_id": "scope_test",
                    "generation_enabled": False,
                    "activation_mode": "automatic",
                    "pending_candidate_id": None,
                    "version": existing_version,
                    "updated_at": "2026-09-10T00:00:00Z",
                }).encode()
            )
        return io.BytesIO(json.dumps({"version": (existing_version or 0) + 1}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    exec(compile(script, "generated-profile-setup", "exec"), {})  # noqa: S102 - execute our generated acceptance command

    assert [request.get_method() for request in requests] == ["GET", "PUT"]
    assert all(request.full_url.endswith("/v1/scopes/scope_test/profile-policy") for request in requests)
    assert all(request.get_header("Authorization") == "Bearer test-token" for request in requests)
    assert json.loads(requests[-1].data) == {
        "generation_enabled": True,
        "activation_mode": "review_required",
        "expected_version": existing_version or 0,
    }


def test_profile_setup_stops_on_authentication_failure_without_put(monkeypatch) -> None:
    script = _profile_script()
    monkeypatch.setenv("POWERCONTEXT_CLAUDE_SCOPE_ID", "scope_test")
    requests = []

    def urlopen(request, **kwargs):
        requests.append(request)
        raise urllib.error.HTTPError(request.full_url, 401, "unauthorized", Message(), None)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    with pytest.raises(SystemExit, match="401"):
        exec(compile(script, "generated-profile-setup", "exec"), {})  # noqa: S102 - execute our generated acceptance command
    assert [request.get_method() for request in requests] == ["GET"]


def test_profile_setup_requires_the_agent_scope_before_any_request(monkeypatch) -> None:
    script = _profile_script()
    monkeypatch.delenv("POWERCONTEXT_CLAUDE_SCOPE_ID", raising=False)
    requests = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, **kwargs: requests.append(request))

    with pytest.raises(SystemExit, match="POWERCONTEXT_CLAUDE_SCOPE_ID"):
        exec(compile(script, "generated-profile-setup", "exec"), {})  # noqa: S102 - execute our generated acceptance command
    assert not requests
