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

"""Behavior contracts for installed observability lifecycle and explicit Server opt-in."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from dataclasses import replace

import pytest
import yaml
from typer.testing import CliRunner

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.observability.cli import app
from powercontext.observability.deployment import ObservabilityError, deployment_lock, initialize, load_state
from powercontext.paths import powercontext_data_dir
from powercontext.server.configuration import ServerConfigurationError, server_settings_context
from powercontext.service.model import DEFINITION_VERSION, OWNERSHIP_MARKER, ServiceDefinition


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    directory = tmp_path / "observability with spaces"
    directory.mkdir()
    monkeypatch.setenv("POWERCONTEXT_OBSERVABILITY_HOME", str(directory))
    # Port ownership is exercised in the real-stack acceptance; isolate this unit fixture.
    monkeypatch.setattr("powercontext.observability.deployment._check_port", lambda port: None)
    initialize(
        directory,
        server_url="http://127.0.0.1:8000",
        token="existing-token",  # noqa: S106 - isolated fixture credential.
        grafana_port=13000,
        gateway_port=14318,
    )
    return directory


def test_unsupported_backend_has_no_side_effects(tmp_path, monkeypatch):
    directory = tmp_path / "absent"
    monkeypatch.setenv("POWERCONTEXT_OBSERVABILITY_HOME", str(directory))
    result = CliRunner().invoke(app, ["up", "--backend", "kubernetes"])
    assert result.exit_code == 1
    assert "available backend: docker-compose" in result.output
    assert not directory.exists()


def test_private_profile_preserves_business_configuration(deployment, monkeypatch, tmp_path):
    for name in list(__import__("os").environ):
        if name.startswith(("OTEL_", "POWERCONTEXT_SERVER_")):
            monkeypatch.delenv(name)
    config = tmp_path / "existing.env"
    config.write_text(
        "POWERCONTEXT_SERVER_DATABASE_KIND=sqlite\n"
        f"POWERCONTEXT_SERVER_DATABASE_URL=sqlite+aiosqlite:///{tmp_path / 'existing.db'}\n"
        "POWERCONTEXT_SERVER_HTTP_PORT=18999\n"
        "POWERCONTEXT_SERVER_AUTH_TOKEN=original-auth\n"
        "POWERCONTEXT_SERVER_ACCESS_MODE=enforced\n"
    )
    original = config.read_bytes()
    with server_settings_context(env_file=config, observability=True) as settings:
        assert settings.http.port == 18999
        assert settings.tracing.enabled
        assert settings.logging.directory == deployment / ".local" / "logs"
        assert settings.logging.otlp_enabled
        assert settings.auth.token is not None
        assert settings.auth.token.get_secret_value() == "original-auth"
        assert isinstance(settings.database, SQLiteConfig)
        assert str(tmp_path / "existing.db") in str(settings.database.url)
    assert config.read_bytes() == original
    assert (deployment / "server.env").stat().st_mode & 0o077 == 0
    with server_settings_context(env_file=config) as settings:
        assert not settings.tracing.enabled
        assert settings.logging.directory == powercontext_data_dir() / "logs"
        assert not settings.logging.file
        assert not settings.logging.otlp_enabled


def test_split_profile_matches_api_and_background_scrape_targets(deployment):
    from powercontext.cli.env_file import read_environment_file

    initialize(
        deployment,
        server_url="http://127.0.0.1:8000",
        token="",
        grafana_port=13000,
        gateway_port=14318,
        background_target="[::1]:8001",
    )
    attributes = read_environment_file(deployment / "server.env")["OTEL_RESOURCE_ATTRIBUTES"]
    assert "powercontext.service.api_target=host.docker.internal:8000" in attributes
    assert "powercontext.service.background_target=[::1]:8001" in attributes


def test_collector_and_gateway_route_otlp_logs(deployment):
    collector = yaml.safe_load((deployment / "collector" / "config.yaml").read_text())
    assert collector["service"]["pipelines"]["logs"]["receivers"] == ["otlp"]
    gateway = (deployment / "gateway" / "nginx.conf").read_text()
    assert "location = /v1/logs { proxy_pass http://collector_backend/v1/logs; }" in gateway


def test_explicit_export_destination_conflict_does_not_expose_credentials(deployment, monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "secret-value-do-not-print")
    with pytest.raises(ServerConfigurationError) as caught, server_settings_context(observability=True):
        pass
    assert "OTEL_EXPORTER_OTLP_HEADERS" in str(caught.value)
    assert "secret-value-do-not-print" not in str(caught.value)


def test_repeat_up_preserves_credentials_targets_and_template_edits(deployment, monkeypatch):
    before = (deployment / ".local" / "gateway-password").read_bytes()
    manifest = deployment / "compose.yaml"
    manifest.write_text(manifest.read_text() + "\n# operator configuration\n")
    monkeypatch.setattr("powercontext.observability.cli.docker_command", lambda: "docker")
    monkeypatch.setattr(
        "powercontext.observability.cli.subprocess.run", lambda *a, **kw: subprocess.CompletedProcess(a, 0)
    )
    monkeypatch.setattr("powercontext.observability.cli.wait_for_backends", lambda *args: None)
    monkeypatch.setattr("powercontext.observability.cli.wait_for_phoenix", lambda *args: None)
    calls = []
    monkeypatch.setattr(
        "powercontext.observability.cli.compose", lambda directory, state, *args, **kw: calls.append(args)
    )
    result = CliRunner().invoke(app, ["up"])
    assert result.exit_code == 0, result.output
    assert calls == [("up", "-d")]
    assert "Observability stack is running" in result.output
    assert "Next: connect your Server" in result.output
    assert "http://127.0.0.1:13000" in result.output
    assert "http://127.0.0.1:6006" in result.output
    assert "powercontext server run --observability" in result.output
    assert "observability check --exercise" in result.output
    assert "phoenix:" in (deployment / "compose.yaml").read_text()
    from powercontext.cli.env_file import read_environment_file

    headers = read_environment_file(deployment / "server.env")["OTEL_EXPORTER_OTLP_HEADERS"]
    assert headers not in result.output
    assert (deployment / ".local" / "gateway-password").read_bytes() == before
    assert "operator configuration" in manifest.read_text()
    assert load_state(deployment)["backend"] == "docker-compose"


def test_up_prints_prefilled_loki_explore_link(deployment, monkeypatch):
    monkeypatch.setattr("powercontext.observability.cli.docker_command", lambda: "docker")
    monkeypatch.setattr(
        "powercontext.observability.cli.subprocess.run", lambda *a, **kw: subprocess.CompletedProcess(a, 0)
    )
    monkeypatch.setattr("powercontext.observability.cli.compose", lambda *args, **kwargs: "")
    monkeypatch.setattr("powercontext.observability.cli.wait_for_backends", lambda *args: None)
    result = CliRunner().invoke(app, ["up"])
    assert result.exit_code == 0, result.output
    lines = [re.sub(r"\x1b\[[0-9;]*m", "", line) for line in result.output.splitlines()]
    url = next(line.strip() for line in lines if "explore?schemaVersion=1" in line)
    assert url.startswith("http://127.0.0.1:13000/explore")
    from urllib.parse import parse_qs, urlsplit

    query = parse_qs(urlsplit(url).query)
    pane = next(iter(json.loads(query["panes"][0]).values()))
    assert pane["queries"][0]["expr"] == '{service_name=~".+"}'
    assert pane["queries"][0]["queryType"] == "range"
    assert pane["panelsState"]["logs"]["visualisationType"] == "logs"
    assert pane["panelsState"]["logs"]["displayedFields"] == [
        "powercontext_service_role",
        "trace_id",
        "___LOG_LINE_BODY___",
    ]


def test_up_prints_prefilled_tempo_explore_link_with_search_tab(deployment, monkeypatch):
    monkeypatch.setattr("powercontext.observability.cli.docker_command", lambda: "docker")
    monkeypatch.setattr(
        "powercontext.observability.cli.subprocess.run", lambda *a, **kw: subprocess.CompletedProcess(a, 0)
    )
    monkeypatch.setattr("powercontext.observability.cli.compose", lambda *args, **kwargs: "")
    monkeypatch.setattr("powercontext.observability.cli.wait_for_backends", lambda *args: None)
    result = CliRunner().invoke(app, ["up"])
    assert result.exit_code == 0, result.output
    lines = [re.sub(r"\x1b\[[0-9;]*m", "", line) for line in result.output.splitlines()]
    urls = [line.strip() for line in lines if "explore?schemaVersion=1" in line]
    from urllib.parse import parse_qs, urlsplit

    panes = [next(iter(json.loads(parse_qs(urlsplit(url).query)["panes"][0]).values())) for url in urls]
    index = panes.index(next(pane for pane in panes if pane["datasource"] == "tempo"))
    tempo_url, tempo_pane = urls[index], panes[index]
    assert tempo_url.startswith("http://127.0.0.1:13000/explore")
    query = tempo_pane["queries"][0]
    assert query["queryType"] == "traceqlSearch"
    assert query["datasource"] == {"type": "tempo", "uid": "tempo"}
    assert query["filters"][0]["tag"] == "service.name"
    assert query["filters"][0]["value"] == ["powercontext-server"]


def test_up_keeps_long_paths_on_aligned_own_lines(deployment, monkeypatch):
    monkeypatch.setattr("powercontext.observability.cli.docker_command", lambda: "docker")
    monkeypatch.setattr(
        "powercontext.observability.cli.subprocess.run", lambda *a, **kw: subprocess.CompletedProcess(a, 0)
    )
    monkeypatch.setattr("powercontext.observability.cli.compose", lambda *args, **kwargs: "")
    monkeypatch.setattr("powercontext.observability.cli.wait_for_backends", lambda *args: None)
    result = CliRunner().invoke(app, ["up"])
    assert result.exit_code == 0, result.output
    lines = [re.sub(r"\x1b\[[0-9;]*m", "", line) for line in result.output.splitlines()]
    config_line = next(line for line in lines if line.strip().startswith("Config"))
    assert "docker-compose" in config_line
    assert config_line.rstrip().endswith("deployment files in:")
    path_line = lines[lines.index(config_line) + 1]
    assert path_line.startswith("            /")


def test_down_preserves_data_and_uses_only_its_saved_project(deployment, monkeypatch):
    sentinel = deployment / ".local" / "keep-data"
    sentinel.write_text("data")
    projects = []

    def stop(directory, state, *args, **kwargs):
        assert args == ("down",)
        projects.append(state["project"])
        return ""

    monkeypatch.setattr("powercontext.observability.cli.compose", stop)
    result = CliRunner().invoke(app, ["down"])
    assert result.exit_code == 0, result.output
    assert sentinel.read_text() == "data"
    assert projects == [load_state(deployment)["project"]]


def test_conflicting_operation_fails_without_deleting_other_lock(deployment):
    with deployment_lock(deployment):
        with pytest.raises(ObservabilityError, match="Another operation"), deployment_lock(deployment):
            pass
        assert (deployment / "operation.lock").exists()
    assert not (deployment / "operation.lock").exists()


def test_anonymous_existing_server_does_not_gain_business_auth(deployment, monkeypatch):
    initialize(deployment, server_url="http://127.0.0.1:8000", token="", grafana_port=13000, gateway_port=14318)
    manifest = yaml.safe_load((deployment / "prometheus" / "prometheus.yaml").read_text())
    assert "authorization" not in manifest["scrape_configs"][0]
    profile = (deployment / "server.env").read_text()
    assert "ACCESS_MODE" not in profile
    assert "AUTH_TOKEN" not in profile
    assert "DATABASE" not in profile


def test_native_service_persists_profile_path_and_preserves_old_definitions(deployment):
    old = ServiceDefinition(
        OWNERSHIP_MARKER, DEFINITION_VERSION, "1.0.0", sys.executable, "http://127.0.0.1:8000", "/data"
    )
    assert "observability_home" not in old.as_dict()
    assert ServiceDefinition.from_dict(old.as_dict()) == old
    opted_in = replace(old, observability_home=str(deployment))
    restored = ServiceDefinition.from_dict(opted_in.as_dict())
    assert restored == opted_in
    assert restored.launcher_arguments()[-2:] == ["--observability-home", str(deployment)]


def test_real_spawned_worker_writes_correlated_json_without_changing_console(tmp_path):
    script = """
import logging
import multiprocessing
from powercontext.server.logging import configure_server_logging, WorkerLogging
from powercontext.server.settings import ServerLoggingConfig

def worker(config):
    WorkerLogging(config)()
    logging.getLogger("powercontext.worker").info("Worker finished", extra={"event":"artifact_processing.worker.returned", "worker_id":"worker-test", "trace_id":"0123456789abcdef0123456789abcdef", "source_content":"must-not-export"})

if __name__ == "__main__":
    config = ServerLoggingConfig(directory=LOG_DIRECTORY, format="console")
    configure_server_logging(config)
    logging.getLogger("powercontext.server").info("Server ready", extra={"event":"test.ready"})
    process = multiprocessing.get_context("spawn").Process(target=worker, args=(config,))
    process.start()
    process.join(15)
    assert process.exitcode == 0
"""
    script = script.replace("LOG_DIRECTORY", repr(str(tmp_path / "logs")))
    entrypoint = tmp_path / "spawn_logs.py"
    entrypoint.write_text(script)
    result = subprocess.run([sys.executable, str(entrypoint)], capture_output=True, text=True, timeout=30, check=True)
    assert "Server ready" in result.stdout
    rows = [json.loads(line) for path in (tmp_path / "logs").glob("*.jsonl") for line in path.read_text().splitlines()]
    worker = next(row for row in rows if row.get("worker_id") == "worker-test")
    assert worker["trace_id"] == "0123456789abcdef0123456789abcdef"
    assert "source_content" not in worker
    assert "must-not-export" not in json.dumps(rows)


def test_unwritable_log_directory_does_not_fail_business_logging(tmp_path):
    script = """
import logging
from powercontext.server.logging import configure_server_logging
from powercontext.server.settings import ServerLoggingConfig
configure_server_logging(ServerLoggingConfig(directory=BAD_PATH))
logging.getLogger("powercontext.server").info("Business still running")
logging.getLogger("powercontext.server").info("Business still running")
"""
    bad_path = tmp_path / "file"
    bad_path.write_text("not a directory")
    result = subprocess.run(
        [sys.executable, "-c", script.replace("BAD_PATH", repr(str(bad_path)))],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert result.stdout.count("Business still running") == 2
    assert result.stderr.count("observability.log_failed") == 1
    assert "Traceback" not in result.stderr


def test_native_opt_in_preserves_registered_data_directory(deployment, tmp_path):
    from powercontext.paths import powercontext_data_dir

    registered = tmp_path / "registered-data"
    with server_settings_context(data_dir=registered, observability=True):
        assert powercontext_data_dir() == registered


def test_docker_mode_generates_override_without_replacing_business_database(deployment):
    initialize(
        deployment,
        server_url="http://127.0.0.1:18086",
        token="",
        grafana_port=13011,
        gateway_port=14329,
        server_mode="docker",
    )
    override = yaml.safe_load((deployment / "server.compose.yaml").read_text())
    business = override["services"]["powercontext"]
    assert "image" not in business
    assert "command" not in business
    assert "entrypoint" not in business
    assert "environment" not in business
    assert business["volumes"] == ["observability-logs:/var/log/powercontext"]
    manifest = yaml.safe_load((deployment / "compose.yaml").read_text())
    assert manifest["services"]["log-permissions"]["command"] == ["chown 10001:10001 /logs && chmod 750 /logs"]
    assert "server-logs:/logs:ro" in manifest["services"]["collector"]["volumes"]


def test_profile_rejects_business_settings_even_when_edited_locally(deployment):
    with (deployment / "server.env").open("a") as output:
        output.write("POWERCONTEXT_SERVER_DATABASE_KIND=sqlite\n")
    with (
        pytest.raises(ServerConfigurationError, match="non-telemetry settings"),
        server_settings_context(observability=True),
    ):
        pass


def test_exercise_waits_for_server_readiness(monkeypatch):
    import httpx

    from powercontext.observability.cli import _sampled_request

    responses = iter([httpx.Response(503), httpx.Response(200)])
    monkeypatch.setattr("powercontext.observability.cli.httpx.get", lambda *args, **kwargs: next(responses))
    monkeypatch.setattr("powercontext.observability.cli.time.sleep", lambda seconds: None)
    _sampled_request("http://127.0.0.1:8000", {})


def test_first_up_uses_existing_server_nested_configuration(deployment, monkeypatch, tmp_path):
    directory = tmp_path / "new-deployment"
    monkeypatch.setenv("POWERCONTEXT_OBSERVABILITY_HOME", str(directory))
    config = tmp_path / "nested.env"
    config.write_text(
        "POWERCONTEXT_SERVER_HTTP='{" + '"host":"127.0.0.1","port":18995' + "}'\n"
        "POWERCONTEXT_SERVER_ACCESS_MODE=enforced\n"
        "POWERCONTEXT_SERVER_AUTH_TOKEN=private-observation-token\n"
    )
    monkeypatch.setattr("powercontext.observability.cli.docker_command", lambda: "docker")
    monkeypatch.setattr(
        "powercontext.observability.cli.subprocess.run", lambda *a, **kw: subprocess.CompletedProcess(a, 0)
    )
    monkeypatch.setattr("powercontext.observability.cli.compose", lambda *args, **kwargs: "")
    monkeypatch.setattr("powercontext.observability.cli.wait_for_backends", lambda *args: None)
    monkeypatch.setattr("powercontext.observability.cli.wait_for_phoenix", lambda *args: None)
    result = CliRunner().invoke(app, ["up", "--server-env", str(config)])
    assert result.exit_code == 0, result.output
    assert load_state(directory)["server_url"] == "http://127.0.0.1:18995"
    assert load_state(directory)["server_env"] == str(config.resolve())
    assert f"--env-file {config} --observability" in result.output
    assert (directory / ".local" / "observe-token").read_text() == "private-observation-token"
    assert "private-observation-token" not in result.output


def test_lowercase_explicit_telemetry_conflict_is_rejected(deployment, tmp_path):
    config = tmp_path / "existing.env"
    config.write_text("powercontext_server_tracing_enabled=false\n")
    with (
        pytest.raises(ServerConfigurationError, match="POWERCONTEXT_SERVER_TRACING_ENABLED"),
        server_settings_context(env_file=config, observability=True),
    ):
        pass


def test_signal_specific_destination_cannot_silently_bypass_profile(deployment, monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://old-destination.invalid/v1/traces")
    with pytest.raises(ServerConfigurationError, match="Signal-specific"), server_settings_context(observability=True):
        pass


def test_nested_explicit_tracing_disable_cannot_be_overwritten(deployment, tmp_path):
    config = tmp_path / "existing.env"
    config.write_text("POWERCONTEXT_SERVER_TRACING='{\"enabled\":false}'\n")
    with (
        pytest.raises(ServerConfigurationError, match="POWERCONTEXT_SERVER_TRACING_ENABLED"),
        server_settings_context(env_file=config, observability=True),
    ):
        pass


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "::1"])  # noqa: S104 - no listener is started.
def test_server_notice_initializes_the_same_address_and_credentials(deployment, monkeypatch, tmp_path, host):
    from powercontext.server.cli import app as server_app

    directory = tmp_path / "notice deployment"
    monkeypatch.setenv("POWERCONTEXT_OBSERVABILITY_HOME", str(directory))
    config = tmp_path / "business settings.env"
    config.write_text(
        "POWERCONTEXT_SERVER_ACCESS_MODE=enforced\n"
        "POWERCONTEXT_SERVER_AUTH_TOKEN=notice-test-token\n"
        "POWERCONTEXT_SERVER_HTTP_PORT=8000\n"
    )
    monkeypatch.setattr("powercontext.server.cli._run_configured_server", lambda settings: None)
    monkeypatch.setattr("powercontext.observability.cli.docker_command", lambda: "docker")
    monkeypatch.setattr(
        "powercontext.observability.cli.subprocess.run", lambda *a, **kw: subprocess.CompletedProcess(a, 0)
    )
    monkeypatch.setattr("powercontext.observability.cli.compose", lambda *args, **kwargs: "")
    monkeypatch.setattr("powercontext.observability.cli.wait_for_backends", lambda *args: None)
    result = CliRunner().invoke(server_app, ["run", "--env-file", str(config), "--host", host, "--port", "18996"])
    assert result.exit_code == 0, result.output
    lines = re.sub(r"\x1b\[[0-9;]*m", "", result.output).splitlines()
    command = shlex.split(
        next(line.strip() for line in lines if line.strip().startswith("powercontext observability up"))
    )
    initialized = CliRunner().invoke(app, command[2:])
    assert initialized.exit_code == 0, initialized.output
    expected_host = "[::1]" if host == "::1" else "127.0.0.1"
    assert load_state(directory)["server_url"] == f"http://{expected_host}:18996"
    assert (directory / ".local" / "observe-token").read_text() == "notice-test-token"
    assert "notice-test-token" not in result.output + initialized.output


@pytest.mark.parametrize("invalid_target", ["http://background:8001", "background:bad", "background:70000"])
def test_invalid_background_target_can_be_corrected_and_retried(deployment, monkeypatch, tmp_path, invalid_target):
    directory = tmp_path / "retry deployment"
    monkeypatch.setenv("POWERCONTEXT_OBSERVABILITY_HOME", str(directory))
    monkeypatch.setattr("powercontext.observability.cli.docker_command", lambda: "docker")
    monkeypatch.setattr(
        "powercontext.observability.cli.subprocess.run", lambda *a, **kw: subprocess.CompletedProcess(a, 0)
    )
    monkeypatch.setattr("powercontext.observability.cli.compose", lambda *args, **kwargs: "")
    monkeypatch.setattr("powercontext.observability.cli.wait_for_backends", lambda *args: None)
    invalid = CliRunner().invoke(app, ["up", "--background-target", invalid_target])
    assert invalid.exit_code == 1
    assert not (directory / "compose.yaml").exists()
    assert not (directory / ".local").exists()
    corrected = CliRunner().invoke(app, ["up", "--background-target", "background:8001"])
    assert corrected.exit_code == 0, corrected.output
    targets = json.loads((directory / ".local" / "targets.json").read_text())
    assert targets[1]["targets"] == ["background:8001"]


@pytest.mark.parametrize("option", ["--phoenix-port", "--phoenix"])
def test_first_up_accepts_documented_phoenix_port_and_existing_alias(deployment, monkeypatch, tmp_path, option):
    directory = tmp_path / "phoenix deployment"
    monkeypatch.setenv("POWERCONTEXT_OBSERVABILITY_HOME", str(directory))
    monkeypatch.setattr("powercontext.observability.cli.docker_command", lambda: "docker")
    monkeypatch.setattr(
        "powercontext.observability.cli.subprocess.run", lambda *a, **kw: subprocess.CompletedProcess(a, 0)
    )
    monkeypatch.setattr("powercontext.observability.cli.compose", lambda *args, **kwargs: "")
    monkeypatch.setattr("powercontext.observability.cli.wait_for_backends", lambda *args: None)
    result = CliRunner().invoke(app, ["up", option, "16006"])
    assert result.exit_code == 0, result.output
    assert load_state(directory)["phoenix_port"] == 16006
    manifest = yaml.safe_load((directory / "compose.yaml").read_text())
    assert manifest["services"]["phoenix"]["ports"] == ["127.0.0.1:16006:6006"]


def test_ipv6_server_address_survives_generated_scrape_and_statistics_configuration(deployment):
    initialize(
        deployment,
        server_url="http://[2001:db8::1]:18080",
        token="",
        grafana_port=13000,
        gateway_port=14318,
    )
    assert load_state(deployment)["metrics_target"] == "[2001:db8::1]:18080"
    targets = json.loads((deployment / ".local" / "targets.json").read_text())
    assert targets[0]["targets"] == ["[2001:db8::1]:18080"]
    statistics = json.loads((deployment / ".local" / "stats.json").read_text())
    assert statistics["server_url"] == "http://[2001:db8::1]:18080"
