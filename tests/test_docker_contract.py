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
from pathlib import Path
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from powercontext.cli.app import create_cli
from powercontext.server.cli import app as server_app

_DOCKERFILE = Path(__file__).resolve().parent.parent / "docker" / "Dockerfile"
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
    monkeypatch.setattr("powercontext.server.cli.configure_server_logging", lambda _config: None)
    monkeypatch.setattr("powercontext.server.cli.configure_server_tracing", lambda _config: tracing)
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
