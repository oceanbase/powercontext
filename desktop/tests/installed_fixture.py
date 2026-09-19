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

"""Isolated no-model Server fixture for installed application acceptance."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
from real_server import HarnessFailure, control_pipe


def wait_ready(client: httpx.Client, process: subprocess.Popen[bytes]) -> None:
    for _ in range(150):
        if process.poll() is not None:
            raise HarnessFailure("installed_server_exited")
        try:
            if client.get("/health/ready").is_success:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise HarnessFailure("installed_server_readiness_timeout")


@contextmanager
def isolated_server(response_loss_counter: Path | None = None) -> Iterator[tuple[httpx.Client, str, str]]:
    desktop = Path(__file__).resolve().parents[1]
    wheels = list((desktop / ".artifacts/server-wheel").glob("*.whl"))
    if len(wheels) != 1:
        raise HarnessFailure("installed_server_wheel_count")
    wheel = wheels[0]
    with tempfile.TemporaryDirectory(prefix="desktop-installed-server-") as directory:
        temp = Path(directory)
        wheel_root = temp / "wheel"
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(wheel_root)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        config = {
            "wheel_root": str(wheel_root),
            "workspace": str(temp),
            "database": f"sqlite+aiosqlite:///{temp / 'data.db'}",
            "token": None,
            "prefix": "",
            "port": port,
            "tls": False,
            "response_loss_path": str(response_loss_counter) if response_loss_counter else None,
        }
        config_path = temp / "server.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        environment = {
            k: v
            for k, v in os.environ.items()
            if k.upper()
            in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "USERPROFILE", "APPDATA", "LOCALAPPDATA"}
        }
        with (temp / "server.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(  # noqa: S603 - own interpreter and fixed fixture script
                [sys.executable, "-I", str(desktop / "tests/real_server.py"), "--serve", str(config_path)],
                stdin=subprocess.PIPE,
                stdout=log,
                stderr=log,
                env=environment,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            control = control_pipe(process)
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=10) as client:
                    wait_ready(client, process)
                    response = client.post(
                        "/v1/scopes",
                        json={
                            "title": "Desktop installed CI",
                            "summary": "Synthetic installed UI acceptance",
                            "idempotency_key": "desktop-installed-ui-scope",
                        },
                    )
                    response.raise_for_status()
                    yield client, response.json()["scope_id"], hashlib.sha256(wheel.read_bytes()).hexdigest()
            finally:
                if process.poll() is None:
                    control.write(b"stop\n")
                    control.flush()
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)
                control.close()
