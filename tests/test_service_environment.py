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

from __future__ import annotations

import os
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from unittest.mock import Mock

import pytest

import powercontext.service.environment as service_environment
from powercontext.service import launcher as service_launcher
from powercontext.service.adapters.base import definition_state
from powercontext.service.environment import ProtectedEnvironmentFileError, load_protected_environment_file
from powercontext.service.model import (
    DEFINITION_VERSION,
    OWNERSHIP_MARKER,
    DefinitionState,
    EnvironmentFileIdentity,
    ServiceDefinition,
)


def _environment_file(tmp_path: Path, content: str = "POWERCONTEXT_SERVER_HTTP_PORT=8123\n") -> Path:
    path = tmp_path / "powercontext.env"
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    if os.name == "nt":
        _secure_windows_file(path)
    return path


def _secure_windows_file(path: Path) -> None:
    account = subprocess.run(
        ["whoami.exe"],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout.strip()
    subprocess.run(
        [  # noqa: S607
            "icacls.exe",
            str(path),
            "/inheritance:r",
            "/grant:r",
            f"{account}:(F)",
            "SYSTEM:(F)",
            "Administrators:(F)",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )


def _definition(tmp_path: Path, environment: Path) -> ServiceDefinition:
    identity = (
        load_protected_environment_file(environment).identity
        if os.name == "nt"
        else EnvironmentFileIdentity.from_path(environment)
    )
    return ServiceDefinition(
        ownership=OWNERSHIP_MARKER,
        definition_version=DEFINITION_VERSION,
        package_version=version("powercontext"),
        python_executable=os.path.abspath(sys.executable),
        endpoint="http://127.0.0.1:8123",
        data_dir=str(tmp_path / "data"),
        env_file=identity,
    )


def test_secure_env_loader_accepts_owned_0600_regular_file(tmp_path: Path) -> None:
    environment = _environment_file(tmp_path)

    loaded = load_protected_environment_file(environment)

    assert loaded.path == environment
    assert loaded.values == {"POWERCONTEXT_SERVER_HTTP_PORT": "8123"}
    if os.name == "nt":
        assert loaded.identity.owner_uid == 0
        assert loaded.identity.mode == 0o666
        assert loaded.identity.owner_sid is not None
    else:
        assert loaded.identity.owner_uid == os.getuid()
        assert loaded.identity.mode == 0o600
        assert loaded.identity.owner_sid is None


def test_secure_env_loader_rejects_group_readable_file(tmp_path: Path) -> None:
    environment = _environment_file(tmp_path)
    if os.name == "nt":
        subprocess.run(
            ["icacls.exe", str(environment), "/grant", "*S-1-5-32-545:(R)"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        expected = "unexpected account"
    else:
        environment.chmod(0o640)
        expected = "accessible only by its owner"

    with pytest.raises(ProtectedEnvironmentFileError, match=expected):
        load_protected_environment_file(environment)


@pytest.mark.skipif(os.name == "nt", reason="O_NOFOLLOW is a POSIX service boundary")
def test_secure_env_loader_rejects_symbolic_link(tmp_path: Path) -> None:
    target = _environment_file(tmp_path)
    link = tmp_path / "linked.env"
    link.symlink_to(target)

    with pytest.raises(ProtectedEnvironmentFileError, match="invalid --env-file"):
        load_protected_environment_file(link)


@pytest.mark.skipif(os.name == "nt", reason="Windows uses ACL identities rather than POSIX user ids")
def test_secure_env_loader_rejects_owner_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    environment = _environment_file(tmp_path)
    getuid = getattr(os, "getuid", None)
    assert getuid is not None
    current_uid = int(getuid())
    monkeypatch.setattr(service_environment.os, "getuid", lambda: current_uid + 1)

    with pytest.raises(ProtectedEnvironmentFileError, match="owned by the current user"):
        load_protected_environment_file(environment)


@pytest.mark.skipif(os.name != "nt", reason="Windows uses ACL owner SIDs rather than POSIX user ids")
def test_secure_env_loader_rejects_windows_owner_sid_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _environment_file(tmp_path)
    monkeypatch.setattr(service_environment, "_windows_file_owner_sid", lambda _path: "S-1-5-21-foreign")

    with pytest.raises(ProtectedEnvironmentFileError, match="owned by the current user"):
        load_protected_environment_file(environment)


@pytest.mark.parametrize("mutation", ["content", "mode"])
def test_secure_env_loader_rejects_recorded_identity_drift(tmp_path: Path, mutation: str) -> None:
    environment = _environment_file(tmp_path)
    identity = _definition(tmp_path, environment).env_file
    assert identity is not None
    if mutation == "content":
        environment.write_text("POWERCONTEXT_SERVER_HTTP_PORT=19000\n", encoding="utf-8")
    else:
        if os.name == "nt":
            pytest.skip("Windows chmod does not change the ACL identity contract")
        environment.chmod(0o400)

    with pytest.raises(ProtectedEnvironmentFileError, match="changed since"):
        load_protected_environment_file(environment, expected=identity)


def test_secure_env_loader_rejects_atomic_replacement_before_open(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("Windows sharing semantics do not permit this POSIX replacement fixture")
    environment = _environment_file(tmp_path)
    identity = EnvironmentFileIdentity.from_path(environment)
    replacement = tmp_path / "replacement.env"
    replacement.write_text("POWERCONTEXT_SERVER_HTTP_PORT=9000\n", encoding="utf-8")
    replacement.chmod(0o600)
    os.replace(replacement, environment)

    with pytest.raises(ProtectedEnvironmentFileError, match="changed since"):
        load_protected_environment_file(environment, expected=identity)


def test_secure_env_loader_uses_opened_inode_when_path_is_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name == "nt":
        pytest.skip("Windows sharing semantics do not permit this POSIX replacement fixture")
    environment = _environment_file(tmp_path)
    identity = EnvironmentFileIdentity.from_path(environment)
    replacement = tmp_path / "replacement.env"
    replacement.write_text("POWERCONTEXT_SERVER_HTTP_PORT=9000\n", encoding="utf-8")
    replacement.chmod(0o600)
    real_open = service_environment.os.open

    def open_then_replace(path: Path, flags: int) -> int:
        descriptor = real_open(path, flags)
        os.replace(replacement, environment)
        return descriptor

    monkeypatch.setattr(service_environment.os, "open", open_then_replace)

    loaded = load_protected_environment_file(environment, expected=identity)

    assert loaded.values == {"POWERCONTEXT_SERVER_HTTP_PORT": "8123"}
    assert environment.read_text(encoding="utf-8") == "POWERCONTEXT_SERVER_HTTP_PORT=9000\n"


def test_secure_env_loader_detects_mutation_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name == "nt":
        pytest.skip("Windows sharing semantics do not permit this POSIX mutation fixture")
    environment = _environment_file(tmp_path)
    real_read = service_environment.os.read
    mutated = False

    def read_then_mutate(descriptor: int, size: int) -> bytes:
        nonlocal mutated
        content = real_read(descriptor, size)
        if not mutated:
            mutated = True
            environment.write_text("POWERCONTEXT_SERVER_HTTP_PORT=19000\n", encoding="utf-8")
        return content

    monkeypatch.setattr(service_environment.os, "read", read_then_mutate)

    with pytest.raises(ProtectedEnvironmentFileError, match="changed while"):
        load_protected_environment_file(environment)


@pytest.mark.skipif(os.name == "nt", reason="Windows permission drift is covered by ACL validation")
def test_definition_state_reports_permission_only_env_drift_as_stale(tmp_path: Path) -> None:
    environment = _environment_file(tmp_path)
    definition = _definition(tmp_path, environment)
    environment.chmod(0o400)

    assert (
        definition_state(
            definition,
            package_version=definition.package_version,
            python_executable=definition.python_executable,
        )
        is DefinitionState.STALE
    )


def test_launcher_rejects_env_drift_without_starting_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _environment_file(tmp_path)
    definition = _definition(tmp_path, environment)
    if os.name == "nt":
        subprocess.run(
            ["icacls.exe", str(environment), "/grant", "*S-1-5-32-545:(R)"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    else:
        environment.chmod(0o640)
    runner = Mock()
    monkeypatch.setattr(service_launcher.server_cli, "_run_configured_server", runner)

    exit_code = service_launcher.main(definition.launcher_arguments()[3:])

    assert exit_code == 1
    runner.assert_not_called()
