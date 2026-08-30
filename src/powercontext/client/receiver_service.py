# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Linux user-service lifecycle for one enrolled remote Skill Receiver."""

# User-service errors retain target-local diagnostics needed to recover safely.
# ruff: noqa: TRY003

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from powercontext.client.skill_receiver import RemoteSkillReceiverConfig

_MANAGED_HEADER = "# Managed by PowerContext remote Skill Receiver."


class ReceiverServiceError(RuntimeError):
    """Reject an unavailable or unsafe user-service operation."""


@dataclass(frozen=True, slots=True)
class ReceiverServiceInstallation:
    """One deterministic systemd user unit installed for an enrolled target."""

    unit_name: str
    unit_path: Path


def install_systemd_user_service(
    config_file: Path,
    config: RemoteSkillReceiverConfig,
    *,
    interval_seconds: float,
) -> ReceiverServiceInstallation:
    """Install and start a target-scoped systemd user service without copying its credential."""

    _require_linux()
    resolved_config = config_file.expanduser().resolve(strict=True)
    powercontext = _powercontext_executable()
    systemctl = _required_executable("systemctl")
    installation = _installation(config.target_id)
    contents = render_systemd_user_service(
        resolved_config,
        config,
        powercontext=powercontext,
        interval_seconds=interval_seconds,
    )
    if installation.unit_path.exists():
        current = installation.unit_path.read_text(encoding="utf-8")
        if not current.startswith(_MANAGED_HEADER):
            raise ReceiverServiceError(f"refusing to replace unmanaged systemd unit: {installation.unit_path}")
    _atomic_write(installation.unit_path, contents)
    _run_systemctl(systemctl, "daemon-reload")
    _run_systemctl(systemctl, "enable", "--now", installation.unit_name)
    return installation


def uninstall_systemd_user_service(target_id: str) -> ReceiverServiceInstallation:
    """Stop and remove only the deterministic PowerContext-managed user unit."""

    _require_linux()
    systemctl = _required_executable("systemctl")
    installation = _installation(target_id)
    if not installation.unit_path.exists():
        raise ReceiverServiceError(f"managed systemd unit does not exist: {installation.unit_path}")
    current = installation.unit_path.read_text(encoding="utf-8")
    if not current.startswith(_MANAGED_HEADER):
        raise ReceiverServiceError(f"refusing to remove unmanaged systemd unit: {installation.unit_path}")
    _run_systemctl(systemctl, "disable", "--now", installation.unit_name)
    installation.unit_path.unlink()
    _run_systemctl(systemctl, "daemon-reload")
    return installation


def render_systemd_user_service(
    config_file: Path,
    config: RemoteSkillReceiverConfig,
    *,
    powercontext: Path,
    interval_seconds: float,
) -> str:
    """Render a secret-free unit that runs the same authenticated Pull Receiver."""

    if interval_seconds < 1:
        raise ValueError("remote watch interval must be at least one second")
    command = " ".join(
        _systemd_quote(value)
        for value in (
            str(powercontext),
            "skill",
            "remote-watch",
            "--config-file",
            str(config_file),
            "--interval",
            f"{interval_seconds:g}",
        )
    )
    return f"""{_MANAGED_HEADER}
[Unit]
Description=PowerContext remote Skill Receiver ({config.target_id})

[Service]
Type=simple
ExecStart={command}
Restart=on-failure
RestartSec=5s
RestartPreventExitStatus=2 3
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
"""


def _installation(target_id: str) -> ReceiverServiceInstallation:
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")).expanduser().resolve(strict=False)
    unit_name = f"powercontext-skill-receiver-{target_id}.service"
    return ReceiverServiceInstallation(unit_name=unit_name, unit_path=root / "systemd" / "user" / unit_name)


def _required_executable(name: str) -> Path:
    resolved = shutil.which(name)
    if resolved is None:
        raise ReceiverServiceError(f"cannot install the Receiver service because {name!r} is not available")
    return Path(resolved).resolve(strict=True)


def _powercontext_executable() -> Path:
    invoked = Path(sys.argv[0]).expanduser()
    if invoked.name == "powercontext" and invoked.is_absolute():
        try:
            return invoked.resolve(strict=True)
        except OSError:
            pass
    return _required_executable("powercontext")


def _atomic_write(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o644)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _run_systemctl(systemctl: Path, *arguments: str) -> None:
    try:
        subprocess.run(  # noqa: S603 - the executable and every argument are resolved without a shell.
            [str(systemctl), "--user", *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "systemctl failed").strip()
        raise ReceiverServiceError(detail) from error


def _systemd_quote(value: str) -> str:
    if "\n" in value or "\0" in value:
        raise ValueError("systemd unit values must remain on one line")
    escaped = value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _require_linux() -> None:
    if sys.platform != "linux":
        raise ReceiverServiceError("systemd user-service installation is supported on Linux only")


__all__ = [
    "ReceiverServiceError",
    "ReceiverServiceInstallation",
    "install_systemd_user_service",
    "render_systemd_user_service",
    "uninstall_systemd_user_service",
]
