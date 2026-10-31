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

"""Detached Source processes with explicit startup, identity checks and graceful stop."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import select
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path

from typing_extensions import override

from powercontext.source_management.config import ManagerConfig, RuntimeConfig, read_config, secret
from powercontext.source_management.durable import RuntimeOwner
from powercontext.source_management.models import ManagementError
from powercontext.source_management.service import protected_write


def _alive(state):
    if not state:
        return False
    owner = state["owner"]
    if owner["boot_id"] != Path("/proc/sys/kernel/random/boot_id").read_text().strip():
        return False
    if owner["machine_id"] != Path("/etc/machine-id").read_text().strip():
        raise ManagementError("foreign-process", "Process state belongs to another machine")
    try:
        stat = Path(f"/proc/{int(owner['pid'])}/stat").read_text().rsplit(")", 1)[1].split()
    except (FileNotFoundError, ProcessLookupError):
        return False
    return stat[0] != "Z" and stat[19] == owner["start_time"]


def process_action(role, config, action):
    """Start or stop only a confirmed local process; repeated starts preserve its state."""
    if sys.platform != "linux":
        raise ManagementError("background-unsupported", "Source background processes currently require Linux")

    import fcntl

    if action == "start":
        secret(config.server.credential_file)
        if isinstance(config, RuntimeConfig):
            secret(config.management.credential_file)
    directory = config.state_dir / (role + "-process")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(directory / "command.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return _operate(role, config, action, directory)
    finally:
        os.close(descriptor)


def _operate(role, config, action, directory):  # noqa: C901 - serialized process control
    state_path, snapshot = directory / "process.json", directory / "config.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    alive = _alive(state)
    if action == "start":
        serialized = config.model_dump_json(indent=2)
        if alive:
            if not state.get("ready") or state.get("stopping"):
                raise ManagementError("process-starting", "Process startup or cleanup has not completed")
            if not snapshot.exists() or snapshot.read_text() != serialized:
                raise ManagementError("configuration-conflict", "Stop the process before changing its configuration")
        else:
            protected_write(snapshot, serialized)
            _spawn(role, snapshot, directory)
            state = json.loads(state_path.read_text())
        return {"state": "running", "pid": state["owner"]["pid"], "configuration": str(snapshot)}
    if action == "stop" and alive:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(5)
            try:
                connection.connect(_address(directory))
                connection.sendall(json.dumps(state["owner"]).encode())
                if connection.recv(16) != b"stopping":
                    raise ManagementError("process-changed", "The running process did not accept the stop request")
            except (ConnectionRefusedError, FileNotFoundError):
                if _alive(state):
                    raise ManagementError("process-unavailable", "Cannot confirm process cleanup") from None
        deadline = time.monotonic() + 45
        while _alive(state):
            if time.monotonic() >= deadline:
                raise ManagementError("stop-timeout", "Process cleanup is unfinished; replacement is blocked")
            time.sleep(0.05)
        alive = False
    elif action not in {"stop", "status"}:
        raise ManagementError("invalid-action", "Choose start, status or stop")
    return {"state": "running" if alive else "stopped", "pid": state["owner"]["pid"] if state else None}


def _spawn(role, snapshot, directory):
    read_fd, write_fd = os.pipe()
    try:
        log_fd = os.open(directory / "process.log", os.O_CREAT | os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(log_fd, "a") as log:
            process = subprocess.Popen(  # noqa: S603 - fixed interpreter and module, no shell
                [sys.executable, "-m", __name__, role, str(snapshot), str(write_fd)],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
                pass_fds=(write_fd,),
            )
        os.close(write_fd)
        write_fd = -1
        try:
            if not select.select([read_fd], [], [], 60)[0] or os.read(read_fd, 16) != b"ready":
                raise ManagementError("startup-failed", "Source process did not become ready; inspect process.log")  # noqa: TRY301
        except BaseException:
            if process.poll() is None:
                process.terminate()
                # Keep identity and locks if cleanup has not completed.
                with suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=45)
            raise
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)


def _address(directory):
    return "\0pc-source-managed-" + hashlib.sha256(os.fsencode(directory.resolve())).hexdigest()


def _control(listener, identity, state_path):
    if sys.platform != "linux":
        raise ManagementError("unsupported", "Source process control requires Linux")
    stopping = False
    while True:
        try:
            connection, _ = listener.accept()
            with connection:
                _, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid != os.getuid():
                    continue
                connection.settimeout(2)
                request = json.loads(connection.recv(4096))
                if request == identity:
                    connection.sendall(b"stopping")
                    if not stopping:
                        stopping = True
                        protected_write(state_path, json.dumps({"owner": identity, "ready": True, "stopping": True}))
                        os.kill(os.getpid(), signal.SIGTERM)
        except (OSError, ValueError):
            if listener.fileno() < 0:
                return


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("manager", "runtime"))
    parser.add_argument("config", type=Path)
    parser.add_argument("ready_fd", type=int)
    args = parser.parse_args()
    config = read_config(args.config, ManagerConfig if args.role == "manager" else RuntimeConfig)
    owner = RuntimeOwner(args.config.parent)
    identity = owner.identity()
    state_path = args.config.parent / "process.json"
    protected_write(state_path, json.dumps({"owner": identity, "ready": False}))
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    notified = False

    def ready():
        nonlocal notified
        if not notified:
            listener.bind(_address(args.config.parent))
            listener.listen(4)
            threading.Thread(target=_control, args=(listener, identity, state_path), daemon=True).start()
            protected_write(state_path, json.dumps({"owner": identity, "ready": True}))
            os.write(args.ready_fd, b"ready")
            notified = True

    try:
        if args.role == "runtime":
            from powercontext.source_management.runtime import run_runtime

            asyncio.run(run_runtime(config, ready=ready))
        else:
            import uvicorn

            from powercontext.source_management.manager import create_manager_app

            class Server(uvicorn.Server):
                @override
                async def startup(self, sockets=None):
                    await super().startup(sockets=sockets)
                    if self.started:
                        ready()

            Server(
                uvicorn.Config(create_manager_app(config), host=config.host, port=config.port, access_log=False)
            ).run()
    except Exception as error:
        print(f"Source process failed ({type(error).__name__}); check deployment configuration", file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        listener.close()
        os.close(args.ready_fd)
        owner.close()


if __name__ == "__main__":
    main()
