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

"""Repository-wide pytest collection controls."""

import os
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from _pytest.mark.expression import Expression

_REAL_E2E_ROOT = Path(__file__).parent / "e2e" / "real_experience_skill"
_DATABASE_MIGRATION_ENV = Path(__file__).parent / "fixtures" / "database_migrations" / "env.py"


@pytest.fixture
def short_tmp_path() -> Iterator[Path]:
    """Leave room for nested checkout caches and backup names on Windows."""
    # pytest's user/session/test-name directories can exhaust MAX_PATH before
    # the fixture's cache, commit hash, and plugin files have been appended.
    # Native database tests also need per-test cleanup to avoid accumulating
    # preallocated engine logs across a complete CI session.
    with TemporaryDirectory(prefix="pc-") as directory:
        yield Path(directory)


@pytest.fixture(autouse=True)
def isolated_client_connection_settings(tmp_path, monkeypatch, request):
    """Isolate daily runtime settings and persistent setup consent, preserving explicit real E2E runs."""

    if not request.config.getoption("run_real_e2e"):
        # Developer runtime overrides must not alter hermetic tests or their subprocesses.
        for name in tuple(os.environ):
            if name.upper().startswith(("POWERCONTEXT_SERVER_", "POWERCONTEXT_CLIENT_", "POWERCONTEXT_CODEX_")):
                monkeypatch.delenv(name)
        # Local fixture servers must remain reachable even when the shell uses a proxy.
        no_proxy = ",".join(
            filter(None, (os.environ.get("NO_PROXY"), os.environ.get("no_proxy"), "localhost,127.0.0.1,::1"))
        )
        monkeypatch.setenv("NO_PROXY", no_proxy)
        monkeypatch.setenv("no_proxy", no_proxy)
        monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(tmp_path / "client-settings.json"))
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
        monkeypatch.setenv("DSH_HOME", str(tmp_path / "dsh-home"))


def pytest_addoption(parser: pytest.Parser) -> None:
    dsh = parser.getgroup("powercontext-dsh")
    dsh.addoption(
        "--require-dsh-runtime",
        action="store_true",
        default=False,
        help="Require both the DSH CLI and native configuration APIs instead of skipping their tests.",
    )
    dsh.addoption(
        "--require-dsh-config-runtime",
        action="store_true",
        default=False,
        help="Require native DSH configuration APIs without requiring the DSH CLI.",
    )
    zcode = parser.getgroup("zcode-host-acceptance")
    zcode.addoption("--run-zcode-acceptance", action="store_true", help="Run real ZCode CLI acceptance.")
    zcode.addoption(
        "--zcode-model-config", type=Path, default=None, help="Existing CLI provider/model JSON for live mode."
    )
    zcode.addoption(
        "--zcode-host-model",
        default=None,
        help="Optional existing provider/model selection for the isolated live profile.",
    )
    zcode.addoption(
        "--zcode-generation-env", type=Path, default=None, help="Existing Server Generation env for live mode."
    )
    zcode.addoption(
        "--zcode-acceptance-output",
        type=Path,
        default=Path(".powercontext/zcode-acceptance"),
        help="Private acceptance evidence directory; each run creates a unique child.",
    )
    group = parser.getgroup("powercontext-real-e2e")
    group.addoption(
        "--run-real-e2e",
        action="store_true",
        dest="run_real_e2e",
        help="Collect tests that use real Codex, model providers, and configured databases.",
    )
    group.addoption(
        "--real-e2e-mode",
        choices=("baseline", "configured", "all"),
        default="all",
        help="Select the real Experience/Skill journey to run.",
    )
    group.addoption(
        "--real-codex-timeout",
        type=int,
        default=600,
        help="Per-session timeout used by real Codex acceptance tests.",
    )
    group.addoption(
        "--real-e2e-env-file",
        type=Path,
        default=Path(".env"),
        help="Environment file used by configured real-service acceptance tests.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Daily tests deselect external hosts; explicit selection fails missing prerequisites."""
    expression = config.option.markexpr or ""
    marker_expression = Expression.compile(expression)
    without_host = marker_expression.evaluate(lambda name, **kwargs: False)
    controlled_requested = (
        marker_expression.evaluate(lambda name, **kwargs: name == "zcode_host_acceptance" and not kwargs)
        and not without_host
    )
    live_requested = (
        marker_expression.evaluate(
            lambda name, **kwargs: name in {"zcode_host_acceptance", "zcode_live_model"} and not kwargs
        )
        and not without_host
    )
    selected = config.getoption("run_zcode_acceptance") or controlled_requested or live_requested
    selected = selected or any("test_zcode_host_acceptance.py" in str(argument) for argument in config.args)
    live = (live_requested and not controlled_requested) or (
        not expression and any("::test_zcode_live_model_acceptance" in str(argument) for argument in config.args)
    )
    deselected = [
        item
        for item in items
        if item.get_closest_marker("zcode_host_acceptance")
        and (not selected or (item.get_closest_marker("zcode_live_model") is not None) != live)
    ]
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = [item for item in items if item not in deselected]


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    # Alembic executes env.py as migration configuration; doctest collection
    # must not import it as a test module.
    if collection_path.resolve() == _DATABASE_MIGRATION_ENV.resolve():
        return True
    if config.getoption("run_real_e2e"):
        return None
    try:
        collection_path.resolve().relative_to(_REAL_E2E_ROOT.resolve())
    except ValueError:
        return None
    return True
