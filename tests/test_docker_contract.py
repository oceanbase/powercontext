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

"""Smoke test that the documented container image starts under its own environment.

The published image binds ``0.0.0.0`` with authentication disabled, so the unauthenticated-bind
guard would refuse to start unless the image also declares the opt-in. This test reads the real
``POWERCONTEXT_SERVER_*`` environment straight from ``docker/Dockerfile`` and drives the documented
``server run`` invocation (no CLI overrides, as ``docker run ... powercontext-server:local`` uses),
so a Dockerfile that drops the opt-in fails here instead of only in a Compose file that sets its own.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml
from typer.testing import CliRunner

from powercontext.cli.app import create_cli
from powercontext.server.cli import app as server_app

_DOCKERFILE = Path(__file__).resolve().parent.parent / "docker" / "Dockerfile"
_DISTRIBUTED_COMPOSE = _DOCKERFILE.with_name("compose.distributed.yaml")
_SERVER_ENV_PATTERN = re.compile(r"(POWERCONTEXT_SERVER_[A-Z0-9_]+)=([^\s\\]+)")


def _dockerfile_server_environment() -> dict[str, str]:
    """Extract the image's ``POWERCONTEXT_SERVER_*`` ENV assignments from the Dockerfile."""

    return dict(_SERVER_ENV_PATTERN.findall(_DOCKERFILE.read_text(encoding="utf-8")))


def test_dockerfile_pins_the_bind_environment_under_test() -> None:
    # Guard the test's own premise: if these keys move, the smoke test below stops meaning anything.
    environment = _dockerfile_server_environment()
    assert environment.get("POWERCONTEXT_SERVER_HTTP_HOST") == "0.0.0.0"  # noqa: S104 - the image's documented bind.


def test_documented_container_invocation_starts_out_of_the_box(monkeypatch: pytest.MonkeyPatch) -> None:
    run_server = Mock()
    tracing = Mock()
    monkeypatch.setattr("powercontext.server.cli._run_server", run_server)
    monkeypatch.setattr("powercontext.server.cli.configure_server_logging", lambda _config, **_kwargs: None)
    monkeypatch.setattr("powercontext.server.cli.configure_server_tracing", lambda _config, **_kwargs: tracing)
    # Reproduce the container's environment exactly, with no ambient POWERCONTEXT_SERVER_* leaking in.
    for name in list(os.environ):
        if name.startswith("POWERCONTEXT_SERVER_"):
            monkeypatch.delenv(name, raising=False)
    for name, value in _dockerfile_server_environment().items():
        monkeypatch.setenv(name, value)

    # The documented `docker run` passes no CLI overrides; the image's CMD is `server run`.
    result = CliRunner().invoke(create_cli([server_app]), ["server", "run"])

    assert result.exit_code == 0, result.output
    run_server.assert_called_once()
    assert run_server.call_args.kwargs["host"] == "0.0.0.0"  # noqa: S104 - the image's documented bind.


def test_distributed_compose_separates_roles_and_model_credentials() -> None:
    document = yaml.safe_load(_DISTRIBUTED_COMPOSE.read_text(encoding="utf-8"))
    services = document["services"]
    assert set(services) == {"migrate", "api-a", "api-b", "scheduler-a", "scheduler-b", "worker-a", "worker-b"}

    # `server migrate` is not a registered command; the migrator must use the supported explicit
    # db-migrate plan/apply workflow with OceanBase's evidence directory and host coordination.
    assert services["migrate"]["entrypoint"] == ["/bin/sh", "-ec"]
    (script,) = services["migrate"]["command"]
    assert "server db-migrate plan" in script
    assert "server db-migrate apply" in script and "--plan-id" in script
    assert "--evidence-dir /var/lib/powercontext/migration-state" in script
    assert "--lock-coordination single-host" in script
    assert "--maintenance-confirmed" in script and "--yes" in script
    assert services["migrate"]["environment"]["POWERCONTEXT_SERVER_DEPLOYMENT_ROLE"] == "api"
    assert services["migrate"]["environment"]["POWERCONTEXT_SERVER_DEPLOYMENT_ID"] == "migrator"
    for name, service in services.items():
        environment = service["environment"]
        assert environment["POWERCONTEXT_SERVER_DATABASE_KIND"] == "oceanbase"
        assert environment["POWERCONTEXT_SERVER_DEPLOYMENT_MODE"] == "distributed"
        if name == "migrate":
            continue
        role = name.split("-", 1)[0]
        assert environment["POWERCONTEXT_SERVER_DEPLOYMENT_ROLE"] == role
        assert environment["POWERCONTEXT_SERVER_DEPLOYMENT_ID"] == name
        if role in {"scheduler", "worker"}:
            assert "ports" not in service
            assert environment["POWERCONTEXT_SERVER_DASHBOARD_ENABLED"] == "false"
            assert environment["POWERCONTEXT_SERVER_MCP_ENABLED"] == "false"
        assert ("POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL" in environment) is (role == "worker")
        assert ("OPENAI_API_KEY" in environment) is (role == "worker")
        assert ("ANTHROPIC_API_KEY" in environment) is (role == "worker")


_COMPOSE_VARIABLE = re.compile(
    r"\$\$|\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([-?])([^}]*))?\}|\$([A-Za-z_][A-Za-z0-9_]*)"
)


def _compose_interpolate(text: str, environment: dict[str, str]) -> str:
    """Apply Compose's own interpolation rules to a raw YAML string.

    ``$$`` becomes a literal ``$`` for the container, ``${NAME:-default}`` falls back to
    the default, and an unset ``$NAME`` -- bare or braced -- is replaced with an empty
    string: the exact behavior that turned ``case "$policy" in`` into ``case "" in``.
    """

    def replace(match: re.Match[str]) -> str:
        if match.group(0) == "$$":
            return "$"
        name = match.group(1) or match.group(4)
        operator, argument = match.group(2), match.group(3)
        value = environment.get(name)
        if operator == "?":
            assert value, f"{name} is required by this Compose file"
            return value
        return value if value else (argument or "")

    return _COMPOSE_VARIABLE.sub(replace, text)


def _run_compose_rendered_migrator(tmp_path: Path, environment: dict[str, str]) -> list[str]:
    """Render the migrator command the way Compose does, then execute it with a stub CLI."""

    document = yaml.safe_load(_DISTRIBUTED_COMPOSE.read_text(encoding="utf-8"))
    (rendered,) = document["services"]["migrate"]["command"]
    # Compose interpolates the YAML string before the container starts; the container's
    # shell then sees the resolved script.
    rendered = _compose_interpolate(rendered, environment)

    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    recorded = tmp_path / "apply-arguments.txt"
    stub = bin_directory / "powercontext"
    stub.write_text(
        "#!/bin/sh\n"
        'if [ "$1 $2 $3" = "server db-migrate plan" ]; then\n'
        '  echo \'{"plan_id": "plan-abc"}\'\n'
        "  exit 0\n"
        "fi\n"
        f'echo "$@" > "{recorded}"\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)

    result = subprocess.run(  # noqa: S603 - fixed argument list, local stub binary.
        ["/bin/sh", "-ec", rendered],
        capture_output=True,
        text=True,
        env={
            **environment,
            "PATH": f"{bin_directory}{os.pathsep}{os.environ.get('PATH', '')}",
        },
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return recorded.read_text(encoding="utf-8").split()


def test_compose_rendered_migrator_runs_with_the_default_backup_policy(tmp_path: Path) -> None:
    """Compose must not blank out the script's own shell variables."""

    # Every deployment variable is present; the shell bookkeeping variables are not host variables.
    arguments = _run_compose_rendered_migrator(
        tmp_path,
        {"POWERCONTEXT_MIGRATION_STATE_DIR": "/tmp/migration-state"},
    )
    assert arguments[:3] == ["server", "db-migrate", "apply"]
    assert arguments[arguments.index("--plan-id") + 1] == "plan-abc"
    assert arguments[arguments.index("--backup") + 1] == "manual"
    assert "--backup-confirmed" in arguments


def test_compose_rendered_migrator_honors_the_skip_backup_policy(tmp_path: Path) -> None:
    arguments = _run_compose_rendered_migrator(
        tmp_path,
        {
            "POWERCONTEXT_MIGRATION_STATE_DIR": "/tmp/migration-state",
            "POWERCONTEXT_MIGRATION_BACKUP_POLICY": "skip",
        },
    )
    assert arguments[arguments.index("--backup") + 1] == "skip"
    assert "--accept-no-backup" in arguments
