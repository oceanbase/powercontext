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

"""Linux systemd user services for the Source manager and managed instances."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Literal

from powercontext.source_management.config import ManagerConfig, RuntimeConfig
from powercontext.source_management.models import ManagementError

Role = Literal["manager", "runtime"]
MARKER = "# Managed by PowerContext Source services v1"


def protected_write(path: Path, content: str, *, immutable: bool = False) -> None:
    """Atomically write private configuration, refusing foreign paths and conflicts."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ManagementError("unsafe-file", "Configuration must be a regular file")
    if path.exists() and immutable:
        if path.read_text(encoding="utf-8") != content:
            raise ManagementError(
                "configuration-conflict", "Existing configuration differs; preserve it or deploy a new instance ID"
            )
        return
    descriptor, name = tempfile.mkstemp(prefix=".pc-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _systemctl(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("systemctl")
    if sys.platform != "linux" or executable is None:
        raise ManagementError("service-unavailable", "Source services require Linux with systemd --user")
    try:
        result = subprocess.run(  # noqa: S603
            [executable, "--user", *arguments],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ManagementError("service-unavailable", "The user service manager did not respond") from error
    if check and result.returncode:
        raise ManagementError("service-operation-failed", "systemd operation failed; inspect the user journal")
    return result


def require_service_support() -> None:
    _systemctl("show-environment")


def _identity(role: Role, config: ManagerConfig | RuntimeConfig) -> tuple[str, Path]:
    identity = config.manager_id if isinstance(config, ManagerConfig) else config.instance_id
    digest = hashlib.sha256(f"{config.server.url}\n{identity}".encode()).hexdigest()[:24]
    name = f"powercontext-source-{role}-{digest}.service"
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")).expanduser()
    return name, root / "systemd" / "user" / name


def _quote(value: str) -> str:
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ManagementError("invalid-service-path", "Service arguments must not contain control characters")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '"'


def render(role: Role, config_file: Path) -> str:
    command = " ".join(
        _quote(part)
        for part in (
            sys.executable,
            "-m",
            "powercontext.source_management.cli",
            role,
            "run",
            "--config",
            str(config_file),
        )
    )
    return f"""{MARKER}
[Unit]
Description=PowerContext Source {role}
After=network.target
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
ExecStart={command}
Restart=on-failure
RestartSec=5
TimeoutStopSec=45
KillMode=control-group
UMask=0077
NoNewPrivileges=true
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
"""


def _service_action(role: Role, config: ManagerConfig | RuntimeConfig, action: str) -> dict[str, Any]:
    """Operate only the exact managed unit; repeated install never restarts it."""
    require_service_support()
    name, path = _identity(role, config)
    snapshot = config.state_dir / f"{role}-service.json"
    contents = render(role, snapshot)
    if path.is_symlink() or (path.exists() and path.read_text(encoding="utf-8") != contents):
        raise ManagementError("service-conflict", "The existing service differs from this configuration")
    if action == "install":
        serialized = json.dumps(config.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
        if snapshot.exists() and snapshot.read_text(encoding="utf-8") != serialized:
            state = _systemctl("is-active", name, check=False).stdout.strip()
            if state not in {"inactive", "failed", "unknown"}:
                raise ManagementError(
                    "configuration-conflict", "Stop the existing service before replacing its configuration"
                )
        protected_write(snapshot, serialized)
        protected_write(path, contents, immutable=True)
        _systemctl("daemon-reload")
        _systemctl("enable", "--now", name)
    elif action in {"stop", "uninstall"}:
        if not path.exists():
            return {"unit": name, "state": "not-installed"}
        _systemctl("stop", name)
        if action == "uninstall":
            _systemctl("disable", name)
            path.unlink()
            if snapshot.is_symlink():
                raise ManagementError("unsafe-file", "Service configuration must not be a symlink")
            snapshot.unlink(missing_ok=True)
            _systemctl("daemon-reload")
    elif action != "status":
        raise ManagementError("invalid-action", "Unknown service operation")
    state = _systemctl("is-active", name, check=False).stdout.strip() or "unknown"
    return {"unit": name, "state": state, "configuration": str(snapshot), "logs": f"journalctl --user -u {name}"}


def service_action(role: Role, config: ManagerConfig | RuntimeConfig, action: str) -> dict[str, Any]:
    """Serialize changes to a unit even when two initializers run concurrently."""
    require_service_support()
    import fcntl

    _, path = _identity(role, config)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return _service_action(role, config, action)
    finally:
        os.close(descriptor)


def launch(role: Role, config: ManagerConfig | RuntimeConfig, mode: str = "background") -> dict[str, Any]:
    """Default to a detached process; system service installation is explicit."""
    if mode == "local-service":
        return service_action(role, config, "install")
    from powercontext.source_management.background import process_action

    return process_action(role, config, "start")
