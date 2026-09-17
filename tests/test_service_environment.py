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
from typing import Any
from unittest.mock import Mock

import pytest

import powercontext.service.environment as service_environment
from powercontext.service import launcher as service_launcher
from powercontext.service._windows_command import run_windows_command
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
    account = (
        subprocess
        .run(
            ["whoami.exe"],  # noqa: S607
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=10,
            check=True,
        )
        .stdout.decode("oem")
        .strip()
    )
    # An elevated shell — what hosted Windows runners use — creates files owned
    # by Administrators rather than by the account itself, which the loader rejects.
    subprocess.run(
        ["icacls.exe", str(path), "/setowner", account],  # noqa: S607
        capture_output=True,
        timeout=10,
        check=True,
    )
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


@pytest.mark.skipif(os.name != "nt", reason="Exercises Windows command output in an isolated console")
@pytest.mark.parametrize("utf8_mode", [0, 1])
@pytest.mark.parametrize("console_code_page", [0, 65001])
def test_windows_env_loader_handles_native_output_encoding(
    tmp_path: Path, utf8_mode: int, console_code_page: int
) -> None:
    environment = _environment_file(tmp_path).rename(tmp_path / "服务配置.env")
    script = """
import ctypes
import subprocess
import sys
from pathlib import Path

from powercontext.service.environment import ProtectedEnvironmentFileError, load_protected_environment_file

code_page = int(sys.argv[2])
if code_page:
    assert ctypes.windll.kernel32.SetConsoleOutputCP(code_page)
path = Path(sys.argv[1])
loaded = load_protected_environment_file(path)
assert loaded.values == {"POWERCONTEXT_SERVER_HTTP_PORT": "8123"}
subprocess.run(
    ["icacls.exe", str(path), "/grant", "*S-1-5-32-545:(R)"],
    capture_output=True, check=True, timeout=10,
)
try:
    load_protected_environment_file(path)
except ProtectedEnvironmentFileError as error:
    assert "unexpected account" in str(error), str(error)
else:
    raise AssertionError("An environment file accessible by Users was accepted")
"""
    result = subprocess.run(
        [sys.executable, "-X", f"utf8={utf8_mode}", "-c", script, str(environment), str(console_code_page)],
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="backslashreplace")
    assert not result.stderr, result.stderr.decode("utf-8", errors="backslashreplace")


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL command decoding")
def test_windows_env_loader_reports_undecodable_acl_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    invalid_output = b"\x81"
    try:
        invalid_output.decode("oem")
    except UnicodeDecodeError:
        pass
    else:
        pytest.skip("The system OEM code page accepts this byte without a trailing byte")
    environment = _environment_file(tmp_path)
    real_run = subprocess.run

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if command[0] == "icacls.exe":
            return subprocess.CompletedProcess(command, 0, invalid_output, b"")
        return real_run(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ProtectedEnvironmentFileError, match="cannot inspect the --env-file ACL: cannot decode icacls"):
        load_protected_environment_file(environment)


@pytest.mark.skipif(os.name != "nt", reason="Windows native command decoding")
def test_run_windows_command_never_silently_drops_undecodable_output() -> None:
    """The caller must never see returncode 0 with stdout missing, as in issue #1627."""

    # Capturing with text=True decoded on the subprocess reader thread, where the
    # UnicodeDecodeError was swallowed and left stdout as None. 0x81 is undefined
    # in the ANSI code page an English console reports and an incomplete lead byte
    # in a DBCS one, so it defeats that capture on either, while the OEM decode
    # this module performs instead either succeeds or reports a command failure.
    command = [sys.executable, "-c", "import sys;sys.stdout.buffer.write(bytes([0x81]))"]

    try:
        result = run_windows_command(command, timeout=30)
    except subprocess.SubprocessError as error:
        assert "cannot decode" in str(error)
    else:
        assert isinstance(result.stdout, str)


def test_secure_env_loader_rejects_group_readable_file(tmp_path: Path) -> None:
    environment = _environment_file(tmp_path)
    if os.name == "nt":
        subprocess.run(
            ["icacls.exe", str(environment), "/grant", "*S-1-5-32-545:(R)"],  # noqa: S607
            capture_output=True,
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
            timeout=10,
            check=True,
        )
    else:
        environment.chmod(0o640)
    runner = Mock()
    monkeypatch.setattr("powercontext.server.cli._run_configured_server", runner)

    exit_code = service_launcher.main(definition.launcher_arguments()[3:])

    assert exit_code == 1
    runner.assert_not_called()
