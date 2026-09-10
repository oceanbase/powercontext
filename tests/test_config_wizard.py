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

"""User-visible file generation flows for the bilingual configuration wizard."""

import json
from collections.abc import Sequence
from pathlib import Path

from typer.testing import CliRunner

import powercontext.cli.config_wizard as config_wizard
from powercontext.cli.config import app
from powercontext.cli.config_wizard import CLIENT, Wizard
from powercontext.cli.config_wizard_agents import AGENT_SPEC_BY_ID
from powercontext.cli.config_wizard_seekdb import SeekDBDependency, SeekDBInstallPlan, SeekDBInstallResult
from powercontext.cli.config_wizard_ui import WizardUI
from powercontext.cli.env_file import parse_environment


class AgentAnswers(WizardUI):
    def __init__(self, *answers: str | bool) -> None:
        super().__init__("en", interactive=False)
        self.answers = iter(answers)
        self.choice_ids: list[tuple[str, ...]] = []
        self.choice_labels: list[tuple[str, ...]] = []
        self.defaults: list[str] = []
        self.prompts: list[str] = []

    def choose(self, en: str, zh: str, choices: Sequence[tuple[str, str, str]], default: str) -> str:
        self.prompts.append(en)
        self.choice_ids.append(tuple(choice[0] for choice in choices))
        self.choice_labels.append(tuple(choice[1] for choice in choices))
        self.defaults.append(default)
        answer = next(self.answers)
        assert isinstance(answer, str)
        return answer

    def confirm(self, en: str, zh: str, default: bool = True) -> bool:
        self.prompts.append(en)
        answer = next(self.answers)
        assert isinstance(answer, bool)
        return answer

    def say(self, en: str, zh: str) -> None:
        pass

    def section(self, en: str, zh: str) -> None:
        pass


def _agent_state(*answers: str | bool, advanced: bool = False) -> tuple[Wizard, AgentAnswers]:
    ui = AgentAnswers(*answers)
    state = Wizard(ui, {}, {}, advanced=advanced)
    state.client = {f"{CLIENT}SERVER_URL": "http://127.0.0.1:8000"}
    return state, ui


def test_agent_menu_repeats_with_configured_agents_removed(monkeypatch) -> None:
    monkeypatch.setattr(config_wizard, "preferred_agent", lambda agents: "codex")
    state, ui = _agent_state("codex", "default", "claude-code", "default", "none")

    config_wizard._agents(state)

    assert state.agents == ("codex", "claude-code")
    assert ui.choice_ids[2] == (
        "claude-code",
        "dsh",
        "openclaw",
        "opencode",
        "pi",
        "hermes",
        "workbuddy",
        "none",
    )
    assert "Capture user prompts as Sources?" not in ui.prompts
    assert ui.defaults[0] == "codex"
    assert ui.defaults[2] == "none"


def test_finishing_without_an_agent_requires_confirmation() -> None:
    state, ui = _agent_state("none", True)

    config_wizard._agents(state)

    assert state.agents == ()
    assert any("no agent connection configuration" in prompt.casefold() for prompt in ui.prompts)


def test_scope_choices_describe_planned_existing_and_unbound_outcomes() -> None:
    state, ui = _agent_state("codex", "new", "none")

    config_wizard._agents(state)

    scope_labels = ui.choice_labels[1]
    assert "Plan a new isolated Scope" in scope_labels[0]
    assert "I already have" in scope_labels[1]
    assert "unbound" in scope_labels[2]


def test_remote_custom_access_defaults_to_all_interfaces_and_explains_external_https(monkeypatch) -> None:
    state, ui = _agent_state()
    state.scenario = "remote"
    state.values[f"{config_wizard.SERVER}HTTP_HOST"] = "127.0.0.1"
    defaults: list[str] = []

    def ask(_en: str, _zh: str, *, default: str, required: bool = False, secret: bool = False) -> str:
        defaults.append(default)
        return default

    monkeypatch.setattr(ui, "ask", ask)
    monkeypatch.setattr(ui, "integer", lambda _en, _zh, *, default, maximum: default)
    monkeypatch.setattr(config_wizard, "_server_url", lambda _ui, _default: "https://memory.example.com")

    host, _, _ = config_wizard._custom_access(state, 8000)

    assert host == "0.0.0.0"  # noqa: S104
    assert defaults == ["0.0.0.0"]  # noqa: S104
    assert any("does not provide HTTPS" in note for note in state.notes)


def test_advanced_capture_opt_out_warns_for_topic_memory() -> None:
    state, _ = _agent_state("codex", False, "default", "none", advanced=True)
    state.features = {"topic-memory"}

    config_wizard._agents(state)

    assert state.client["POWERCONTEXT_CODEX_CAPTURE_PROMPTS"] == "false"
    assert any("will not drive" in note for note in state.notes)


def test_agent_client_fields_follow_real_contract() -> None:
    cases = (
        ("dsh", "POWERCONTEXT_DSH_BASE_URL", "POWERCONTEXT_DSH_CAPTURE_PROMPTS", "POWERCONTEXT_DSH_SCOPE_ID"),
        (
            "opencode",
            "POWERCONTEXT_OPENCODE_BASE_URL",
            "POWERCONTEXT_OPENCODE_CAPTURE_PROMPTS",
            "POWERCONTEXT_OPENCODE_SCOPE_ID",
        ),
        ("pi", "POWERCONTEXT_PI_BASE_URL", "POWERCONTEXT_PI_CAPTURE_PROMPTS", "POWERCONTEXT_PI_SCOPE_ID"),
        (
            "hermes",
            "POWERCONTEXT_HERMES_BASE_URL",
            "POWERCONTEXT_HERMES_CAPTURE_TURNS",
            "POWERCONTEXT_HERMES_SCOPE_ID",
        ),
        (
            "workbuddy",
            "POWERCONTEXT_WORKBUDDY_SERVER_URL",
            "POWERCONTEXT_WORKBUDDY_CAPTURE_PROMPTS",
            "POWERCONTEXT_WORKBUDDY_SCOPE_ID",
        ),
    )
    for agent, url_name, capture_name, scope_name in cases:
        state, _ = _agent_state()
        state.client[f"{CLIENT}API_TOKEN"] = "test-token"
        config_wizard._agent_fields(
            state,
            AGENT_SPEC_BY_ID[agent],
            "http://127.0.0.1:8000",
            capture=True,
            scope="SCOPE_TEST",
        )
        assert state.client[url_name] == "http://127.0.0.1:8000"
        assert state.client[capture_name] == "true"
        assert state.client[scope_name] == "SCOPE_TEST"


def test_openclaw_fields_remain_plugin_settings() -> None:
    state, _ = _agent_state()
    state.client[f"{CLIENT}API_TOKEN"] = "test-token"

    config_wizard._agent_fields(
        state,
        AGENT_SPEC_BY_ID["openclaw"],
        "http://127.0.0.1:8000",
        capture=True,
        scope="SCOPE_TEST",
    )

    assert state.agent_settings["openclaw"] == {
        "endpoint": "http://127.0.0.1:8000",
        "autoCapture": True,
        "scopeId": "SCOPE_TEST",
    }
    assert not any(name.startswith("POWERCONTEXT_OPENCLAW_") for name in state.client)


def test_openclaw_next_steps_use_plugin_configuration_contract(tmp_path: Path) -> None:
    output = tmp_path / "server.env"
    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--output", str(output)],
        input=f"sqlite\n{tmp_path / 'context.db'}\nlocal\nbase\ny\nopenclaw\nnew\nnone\ny\n",
    )

    assert result.exit_code == 0, result.output
    client = parse_environment(output.read_text())
    assert not any(name.startswith("POWERCONTEXT_OPENCLAW_") for name in client)
    steps = output.with_name("server.env.next-steps.md").read_text()
    assert "powercontext setup openclaw" in steps
    assert "--server-url http://127.0.0.1:8000" in steps
    assert "plugins.entries.memory-powercontext.config.endpoint" in steps
    assert "plugins.entries.memory-powercontext.config.autoCapture true" in steps
    assert "plugins.entries.memory-powercontext.config.scopeId '<returned-scope-id>'" in steps
    assert "Authorization: Bearer $POWERCONTEXT_CLIENT_API_TOKEN" in steps


def test_base_wizard_writes_private_environment_without_models(tmp_path: Path) -> None:
    output = tmp_path / "server.env"
    database = tmp_path / "context.db"
    # Storage, path, local scene, base capability, no dashboard, no Agent, save.
    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--output", str(output)],
        input=f"sqlite\n{database}\nlocal\nbase\nn\nnone\ny\ny\n",
    )
    assert result.exit_code == 0, result.output
    values = parse_environment(output.read_text())
    assert values["POWERCONTEXT_SERVER_DATABASE_KIND"] == "sqlite"
    assert values["POWERCONTEXT_SERVER_DASHBOARD_ENABLED"] == "false"
    assert "POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL" not in values
    assert "API key" not in result.output
    assert "Model service" not in result.output
    assert not database.exists()
    assert output.stat().st_mode & 0o777 == 0o600
    assert CliRunner().invoke(app, ["validate", "--env-file", str(output)]).exit_code == 0


def test_cancel_in_chinese_does_not_write_files(tmp_path: Path) -> None:
    output = tmp_path / "server.env"
    result = CliRunner().invoke(
        app,
        ["init", "--language", "zh", "--output", str(output)],
        input=f"sqlite\n{tmp_path / 'context.db'}\nlocal\nbase\nn\nnone\ny\nn\n",
    )
    assert result.exit_code == 0, result.output
    assert "配置向导" in result.output
    assert "未写入" in result.output
    assert not list(tmp_path.iterdir())


def test_existing_server_skips_server_models_and_storage(tmp_path: Path) -> None:
    output = tmp_path / "client.env"
    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--output", str(output)],
        input="remote\nhttps://memory.example.com\nsecret-bearer\nnone\ny\ny\n",
    )
    assert result.exit_code == 0, result.output
    values = parse_environment(output.read_text())
    assert values["POWERCONTEXT_CLIENT_SERVER_URL"] == "https://memory.example.com"
    assert values["POWERCONTEXT_CLIENT_API_TOKEN"] == "secret-bearer"  # noqa: S105
    assert not any(key.startswith("POWERCONTEXT_SERVER_") for key in values)
    assert "secret-bearer" not in result.output
    assert "Generation" not in result.output


def test_existing_environment_can_be_reused_without_retyping_models(tmp_path: Path) -> None:
    output = tmp_path / "server.env"
    original = "# Keep my comments\nPOWERCONTEXT_SERVER_DATABASE_KIND=sqlite\nCUSTOM_FLAG=untouched\n"
    output.write_text(original)
    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--output", str(output)],
        input=f"sqlite\n{tmp_path / 'context.db'}\nreuse\ny\n",
    )
    assert result.exit_code == 0, result.output
    assert "CUSTOM_FLAG=untouched" in output.read_text()
    assert "# Keep my comments" in output.read_text()
    assert "Model service" not in result.output
    assert "Keep other existing settings and review" in result.output
    assert "Edit selected modules" in result.output
    assert "Confirm every setting" in result.output
    assert len(list(tmp_path.glob("server.env.bak-*"))) == 1


def test_eof_never_falls_back_to_a_template(tmp_path: Path) -> None:
    output = tmp_path / "server.env"
    result = CliRunner().invoke(app, ["init", "--language", "en", "--output", str(output)], input="")
    assert result.exit_code != 0
    assert not output.exists()


def test_seekdb_install_runs_behind_prompts_and_finishes_after_save(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "server.env"
    events: list[str] = []

    class Task:
        def done(self) -> bool:
            events.append("done")
            return "wait" in events

        def phase(self) -> str:
            return "installing"

        def elapsed_seconds(self) -> float:
            return 2.0

        def wait(self, timeout=None) -> SeekDBInstallResult:
            events.append("wait")
            return SeekDBInstallResult("ready", version="1.3.0")

        def cancel(self) -> None:
            events.append("cancel")

    plan = SeekDBInstallPlan("uv", "/tool/bin/python", ("pylibseekdb<2,>=1.3",), prefer_aliyun=True)
    monkeypatch.setattr(config_wizard, "inspect_seekdb_dependency", lambda: SeekDBDependency("installable", plan=plan))
    monkeypatch.setattr(config_wizard, "start_seekdb_install", lambda _plan: Task())
    monkeypatch.setattr(
        WizardUI,
        "wait_for_activity",
        lambda self, done, phase, elapsed: (events.append("activity"), done(), phase(), elapsed()),
    )

    result = CliRunner().invoke(
        app,
        ["init", "--language", "zh", "--output", str(output)],
        input=f"seekdb\n{tmp_path / 'seekdb'}\ny\nlocal\nbase\nn\nnone\ny\ny\n",
    )

    assert result.exit_code == 0, result.output
    assert "在后台开始安装" in result.output
    assert "阿里云镜像" in result.output
    assert "seekdb 依赖安装完成，版本 1.3.0" in result.output  # noqa: RUF001
    assert result.output.index("已保存") < result.output.index("seekdb 依赖安装完成")
    assert "activity" in events
    assert "wait" in events
    assert "cancel" not in events


def test_seekdb_install_failure_prints_one_manual_command(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "server.env"

    class Task:
        def done(self) -> bool:
            return True

        def phase(self) -> str:
            return "failed"

        def elapsed_seconds(self) -> float:
            return 1.0

        def wait(self, timeout=None) -> SeekDBInstallResult:
            return SeekDBInstallResult(
                "failed",
                reason="dependency installation failed",
                manual_command="uv pip install --python /tool/bin/python pylibseekdb",
            )

    plan = SeekDBInstallPlan("uv", "/tool/bin/python", ("pylibseekdb",))
    monkeypatch.setattr(config_wizard, "inspect_seekdb_dependency", lambda: SeekDBDependency("installable", plan=plan))
    monkeypatch.setattr(config_wizard, "start_seekdb_install", lambda _plan: Task())

    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--output", str(output)],
        input=f"seekdb\n{tmp_path / 'seekdb'}\ny\nlocal\nbase\nn\nnone\ny\ny\n",
    )

    assert result.exit_code == 0, result.output
    assert "seekdb dependency installation failed" in result.output
    assert result.output.count("uv pip install --python /tool/bin/python pylibseekdb") == 1


def test_dashboard_finish_shows_new_token_once_and_clear_old_bindings(tmp_path: Path) -> None:
    output = tmp_path / "server.env"
    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--output", str(output)],
        input=f"sqlite\n{tmp_path / 'context.db'}\nlocal\nbase\ny\ncodex\ndefault\nclaude-code\ndefault\nnone\ny\n",
    )
    assert result.exit_code == 0, result.output
    server_values = parse_environment(output.read_text())
    client_values = server_values
    token = server_values["POWERCONTEXT_SERVER_AUTH_TOKEN"]
    saved_summary = result.output.split("Connection details", maxsplit=1)[1]
    assert result.output.count(token) == 1
    assert "Dashboard: http://127.0.0.1:8000/dashboard/home" in saved_summary
    assert "shown only this time" in saved_summary
    assert f"POWERCONTEXT_SERVER_AUTH_TOKEN in {output}" in saved_summary
    assert token not in output.with_name("server.env.next-steps.md").read_text()
    assert f"`POWERCONTEXT_SERVER_AUTH_TOKEN` in `{output}`" in output.with_name("server.env.next-steps.md").read_text()
    assert server_values["POWERCONTEXT_SERVER_ACCESS_MODE"] == "enforced"
    assert client_values["POWERCONTEXT_CODEX_AUTHORIZATION"] == f"Bearer {token}"
    assert client_values["POWERCONTEXT_CLAUDE_AUTHORIZATION"] == f"Bearer {token}"
    assert "POWERCONTEXT_CODEX_SCOPE_ID" not in client_values
    assert "POWERCONTEXT_CODEX_CONTEXT_ASSEMBLY" not in client_values
    assert client_values["POWERCONTEXT_SERVER_AUTH_TOKEN"] == token
    assert output.stat().st_mode & 0o777 == 0o600


def test_full_memory_configuration_shares_provider_and_adds_profile_recall(tmp_path: Path) -> None:
    output = tmp_path / "server.env"
    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--output", str(output)],
        input=(
            f"sqlite\n{tmp_path / 'context.db'}\nlocal\nfull\nn\n"
            "bailian\n\n\nexample-test-key\ny\n\nrecommended\ncodex\nexisting\nproject:demo\nnone\ny\n"
        ),
    )
    assert result.exit_code == 0, result.output
    values = parse_environment(output.read_text())
    inference = "POWERCONTEXT_SERVER_INFERENCE_"
    assert values[inference + "GENERATION_MODEL"] == "openai-chat:qwen-plus"
    assert values[inference + "EMBEDDING_MODEL"] == "openai:text-embedding-v4"
    assert values[inference + "GENERATION_HEADERS"] == values[inference + "EMBEDDING_HEADERS"]
    assert values["POWERCONTEXT_SERVER_RUNTIME_MEMORY_SCHEDULE_SECONDS"] == "60"
    assert values["POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_SCHEDULE_SECONDS"] == "300"
    assert values["POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_SCHEDULE_SECONDS"] == "900"
    assert "skill" in json.loads(values["POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_FAMILIES"])
    assert "example-test-key" not in result.output
    assert "Memory, Topic Memory, Profile, Experience, Skill" in result.output
    assert "Generation and Embedding" in result.output
    assert "Memory every 1 minute" in result.output
    assert "Topic Memory every 5 minutes" in result.output
    assert "Experience every 15 minutes" in result.output
    assert "daily at 02:00" in result.output
    assert CliRunner().invoke(app, ["validate", "--env-file", str(output)]).exit_code == 0
    client = parse_environment(output.read_text())
    assembly = json.loads(client["POWERCONTEXT_CODEX_CONTEXT_ASSEMBLY"])
    families = [section["family"] for section in assembly["sections"]]
    assert families == ["memory", "profile", "topic-memory", "experience"]
    assert len(families) == len(set(families))
    assert client[inference + "GENERATION_MODEL"] == "openai-chat:qwen-plus"
    assert "profile-policy" in output.with_name("server.env.next-steps.md").read_text()
    assert not (tmp_path / "context.db").exists()


def test_custom_topic_memory_does_not_require_embedding(tmp_path: Path) -> None:
    output = tmp_path / "server.env"
    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--output", str(output)],
        input=(
            f"sqlite\n{tmp_path / 'context.db'}\nlocal\ncustom\n"
            "n\ny\nn\nn\nn\nn\nn\nn\nbailian\n\n\nexample-test-key\nrecommended\nnone\ny\ny\n"
        ),
    )
    assert result.exit_code == 0, result.output
    values = parse_environment(output.read_text())
    assert values["POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_SCHEDULE_SECONDS"] == "300"
    assert "POWERCONTEXT_SERVER_RUNTIME_MEMORY_SCHEDULE_SECONDS" not in values
    assert "POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL" not in values
    assert "Embedding connection" not in result.output


def test_client_only_output_cannot_reuse_server_secrets_file(tmp_path: Path) -> None:
    output = tmp_path / "server.env"
    original = "POWERCONTEXT_SERVER_DATABASE_KIND=sqlite\nOPENAI_API_KEY=example-test-key\n"
    output.write_text(original)
    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--output", str(output)],
        input="remote\nhttps://memory.example.com\nexample-token\nnone\ny\n",
    )
    assert result.exit_code != 0
    assert output.read_text() == original
    assert not output.with_name("server.env.next-steps.md").exists()


def test_ssh_forwarding_configures_the_agent_on_the_other_computer(tmp_path: Path) -> None:
    output = tmp_path / "server.env"
    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--output", str(output)],
        input=(f"sqlite\n{tmp_path / 'context.db'}\nremote\nbase\ny\nssh\nt1\n18000\ncodex\nother\n\nnew\nnone\ny\n"),
    )
    assert result.exit_code == 0, result.output
    values = parse_environment(output.read_text())
    assert values["POWERCONTEXT_SERVER_HTTP_PORT"] == "8000"
    client = parse_environment(output.read_text())
    assert "POWERCONTEXT_CODEX_SERVER_URL" not in client
    assert client["POWERCONTEXT_CLIENT_SERVER_URL"] == "http://127.0.0.1:18000"
    steps = output.with_name("server.env.next-steps.md").read_text()
    assert '"url": "http://127.0.0.1:18000/mcp"' in steps
    tunnel = "ssh -N -L 18000:127.0.0.1:8000 t1"
    assert tunnel in steps
    saved_summary = result.output.split("Connection details", maxsplit=1)[1]
    assert tunnel in saved_summary
    assert "Dashboard: http://127.0.0.1:18000/dashboard/home" in saved_summary
    assert "MCP endpoint: http://127.0.0.1:18000/mcp" in saved_summary


def test_finish_does_not_reveal_an_existing_server_token(tmp_path: Path) -> None:
    output = tmp_path / "server.env"
    existing_token = "existing-server-token"  # noqa: S105
    output.write_text(
        "POWERCONTEXT_SERVER_DATABASE_KIND=sqlite\n"
        f"POWERCONTEXT_SERVER_DATABASE_URL=sqlite+aiosqlite:///{tmp_path / 'context.db'}\n"
        "POWERCONTEXT_SERVER_HTTP_HOST=127.0.0.1\n"
        "POWERCONTEXT_SERVER_HTTP_PORT=8000\n"
        "POWERCONTEXT_SERVER_DASHBOARD_ENABLED=true\n"
        "POWERCONTEXT_SERVER_MCP_ENABLED=true\n"
        "POWERCONTEXT_SERVER_MCP_PATH=/mcp\n"
        "POWERCONTEXT_SERVER_ACCESS_MODE=enforced\n"
        f"POWERCONTEXT_SERVER_AUTH_TOKEN={existing_token}\n"
    )

    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--output", str(output)],
        input=f"sqlite\n{tmp_path / 'context.db'}\nreuse\ny\n",
    )

    assert result.exit_code == 0, result.output
    saved_summary = result.output.split("Connection details", maxsplit=1)[1]
    assert existing_token not in result.output
    assert "Dashboard: http://127.0.0.1:8000/dashboard/home" in saved_summary
    assert f"POWERCONTEXT_SERVER_AUTH_TOKEN in {output}" in saved_summary


def test_topic_rejects_unsupported_existing_request_settings_without_leaking_values(tmp_path: Path) -> None:
    output = tmp_path / "server.env"
    original = (
        "POWERCONTEXT_SERVER_DATABASE_KIND=sqlite\n"
        "POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=test\n"
        'POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL_SETTINGS=\'{"extra_body":{"secret":"example-test-key"}}\'\n'
        "POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_SCHEDULE_SECONDS=60\n"
        "POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_FAMILIES='[\"topic-memory\"]'\n"
    )
    output.write_text(original)
    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--output", str(output)],
        input=f"sqlite\n{tmp_path / 'context.db'}\nreuse\ny\n",
    )
    assert result.exit_code != 0
    assert "incompatible" in result.output
    assert "example-test-key" not in result.output
    assert output.read_text() == original
    assert not output.with_name("server.env.next-steps.md").exists()


def test_generation_only_existing_configuration_can_edit_model_connection(tmp_path: Path) -> None:
    output = tmp_path / "server.env"
    output.write_text("POWERCONTEXT_SERVER_DATABASE_KIND=sqlite\nPOWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=test\n")
    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--output", str(output)],
        input=f"sqlite\n{tmp_path / 'context.db'}\nedit\nmodels\ny\ndone\ny\n",
    )
    assert result.exit_code == 0, result.output
    assert "Keep the existing Generation configuration?" in result.output


def test_capability_edit_does_not_unconditionally_reopen_models_or_processing(
    monkeypatch,
) -> None:
    state, _ = _agent_state("capabilities", "base", "done")
    calls: list[str] = []
    monkeypatch.setattr(config_wizard, "_models", lambda state, required_features=None: calls.append("models"))
    monkeypatch.setattr(config_wizard, "_processing", lambda state: calls.append("processing"))

    config_wizard._edit_modules(state)

    assert calls == []


def test_capability_edit_collects_only_new_missing_model_roles(monkeypatch) -> None:
    state, _ = _agent_state("capabilities", "done")
    calls: list[set[str] | None] = []
    monkeypatch.setattr(config_wizard, "_capabilities", lambda state: state.features.add("vector"))
    monkeypatch.setattr(
        config_wizard,
        "_models",
        lambda state, required_features=None: calls.append(required_features),
    )

    config_wizard._edit_modules(state)

    assert calls == [{"vector"}]


def test_capability_edit_does_not_reask_for_configured_generation(monkeypatch) -> None:
    values = {"POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL": "openai-chat:qwen-plus"}
    ui = AgentAnswers("capabilities", "done")
    state = Wizard(ui, dict(values), dict(values))
    calls: list[set[str] | None] = []
    monkeypatch.setattr(config_wizard, "_capabilities", lambda state: state.features.add("topic-memory"))
    monkeypatch.setattr(
        config_wizard,
        "_models",
        lambda state, required_features=None: calls.append(required_features),
    )

    config_wizard._edit_modules(state)

    assert calls == []


def test_processing_choice_says_selected_automatic_capabilities_are_already_enabled(tmp_path: Path) -> None:
    output = tmp_path / "server.env"
    result = CliRunner().invoke(
        app,
        ["init", "--language", "zh", "--output", str(output)],
        input=(
            f"sqlite\n{tmp_path / 'context.db'}\nlocal\ncustom\n"
            "n\ny\nn\nn\nn\nn\nn\nn\nbailian\n\n\nkey\nrecommended\nnone\ny\nn\n"
        ),
    )
    assert result.exit_code == 0, result.output
    assert "已启用自动处理" in result.output
    assert "使用各 Artifact 推荐周期" in result.output
    assert "Topic Memory 每 5 分钟" in result.output
    assert "逐项自定义周期" in result.output
    assert "关闭后台处理" not in result.output


def test_agents_are_selected_one_at_a_time_and_get_independent_scope_plans(tmp_path: Path) -> None:
    output = tmp_path / "server.env"
    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--output", str(output)],
        input=(f"sqlite\n{tmp_path / 'context.db'}\nlocal\nbase\nn\ncodex\nnew\nclaude-code\nnew\nnone\ny\n"),
    )
    assert result.exit_code == 0, result.output
    assert result.output.count("Select an Agent to configure") == 3
    client = parse_environment(output.read_text())
    assert "POWERCONTEXT_CODEX_SCOPE_ID" not in client
    assert "POWERCONTEXT_CLAUDE_SCOPE_ID" not in client
    steps = output.with_name("server.env.next-steps.md").read_text()
    assert '"title": "codex-' in steps
    assert '"title": "claude-code-' in steps
    assert "POST /v1/scopes" in steps
    assert "use the returned scope_id" in steps


def test_existing_scope_is_requested_separately_for_each_agent(tmp_path: Path) -> None:
    output = tmp_path / "server.env"
    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--output", str(output)],
        input=(
            f"sqlite\n{tmp_path / 'context.db'}\nlocal\nbase\nn\n"
            "codex\nexisting\nSCOPE_CODEX\nclaude-code\nexisting\nSCOPE_CLAUDE\nnone\ny\n"
        ),
    )
    assert result.exit_code == 0, result.output
    client = parse_environment(output.read_text())
    assert client["POWERCONTEXT_CODEX_SCOPE_ID"] == "SCOPE_CODEX"
    assert client["POWERCONTEXT_CLAUDE_SCOPE_ID"] == "SCOPE_CLAUDE"
