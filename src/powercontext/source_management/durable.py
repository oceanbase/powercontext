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

"""Exclusive process ownership for local services and plugin instances."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from powercontext.source_management.models import ManagementError


def protected_directory(directory: Path) -> None:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink():
        raise ManagementError("invalid-storage", "Runtime state directory must not be a symlink")
    os.chmod(directory, 0o700)


class RuntimeOwner:
    """A process lock held across control reconnects and all business Contexts."""

    def __init__(self, directory: Path) -> None:
        import fcntl

        protected_directory(directory)
        self.directory = directory.resolve()
        path = directory / "owner.lock"
        if path.is_symlink():
            raise ManagementError("invalid-storage", "Runtime owner lock must not be a symlink")
        self.stream = path.open("a+b")
        os.chmod(path, 0o600)
        try:
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            self.stream.close()
            raise ManagementError(
                "instance-running", "This execution location already has a runtime owner", 409
            ) from error

    def close(self) -> None:
        self.stream.close()

    def identity(self) -> dict[str, Any]:
        """A local process identity; PID reuse and copied state cannot authorize recovery."""
        stat = Path(f"/proc/{os.getpid()}/stat").read_text().rsplit(")", 1)[1].split()
        return {
            "pid": os.getpid(),
            "start_time": stat[19],
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "machine_id": Path("/etc/machine-id").read_text().strip(),
            "state_dir": str(self.directory),
        }

    def exited(self, previous: dict[str, Any]) -> bool:
        owner = previous.get("owner")
        if owner is None or owner.get("state_dir") != str(self.directory):
            return False
        current = self.identity()
        if owner.get("machine_id") != current["machine_id"]:
            return False
        if owner.get("boot_id") != current["boot_id"]:
            return True
        try:
            stat = Path(f"/proc/{int(owner['pid'])}/stat").read_text().rsplit(")", 1)[1].split()
        except (FileNotFoundError, ProcessLookupError):
            return True
        return stat[0] == "Z" or stat[19] != owner.get("start_time")


class InstanceOwner(RuntimeOwner):
    """Prevent multiple local Runtimes for the same Server and instance identity."""

    def __init__(self, server_url: str, instance_id: str) -> None:
        from hashlib import sha256

        from powercontext.paths import powercontext_data_dir

        key = sha256((server_url.rstrip("/") + "\0" + instance_id).encode()).hexdigest()
        super().__init__(powercontext_data_dir() / "source-owners" / key)
