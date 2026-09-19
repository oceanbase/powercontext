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

"""Run the native Desktop adapter against an isolated, built-wheel SQLite Server.

No product command starts a Server. This explicit test harness owns its disposable processes and data.
"""

# All subprocesses below are fixed local test executables, never renderer-provided commands.
# ruff: noqa: S603, S607
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import secrets
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import IO


class HarnessFailure(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"Desktop isolated fixture failed ({code}): {detail}")


def probe_measurement(output: str) -> dict[str, object]:
    measurement = json.loads(output)
    if measurement.get("result") != "passed":
        raise HarnessFailure("native_probe_result")
    return measurement["performance"]


def fixture_provider(config, admin):
    from powercontext.server.authentication import (
        AuthenticationRejectedError,
        AuthenticationResult,
        ProviderReadiness,
    )
    from powercontext.server.authz import PrincipalRef

    class FixtureProvider:
        async def authenticate(self, request):
            if request.headers.get("authorization") == f"Bearer {config['reader_token']}":
                return AuthenticationResult(
                    subject=PrincipalRef(type="user", id="desktop-fixture-reader"),
                    credential_id="desktop-test-reader",
                )
            if request.headers.get("authorization") != f"Bearer {config['token']}":
                raise AuthenticationRejectedError
            subject = (
                PrincipalRef(type="user", id="desktop-fixture-changed")
                if await asyncio.to_thread(Path(config["identity_change_path"]).exists)
                else admin
            )
            return AuthenticationResult(subject=subject, credential_id="desktop-test-provider")

        async def readiness(self):
            return ProviderReadiness(ready=True)

    return FixtureProvider()


async def forward_with_response_loss(app, scope, receive, send, control_path):
    """Commit the real request, then deliberately truncate its response on the wire."""
    control = Path(control_path) if control_path else None
    if (
        scope["type"] != "http"
        or scope["path"] != "/v1/memory/remember"
        or control is None
        or not await asyncio.to_thread(control.exists)
    ):
        await app(scope, receive, send)
        return
    count = int(await asyncio.to_thread(control.read_text, encoding="utf-8"))
    await asyncio.to_thread(control.write_text, str(count + 1), encoding="utf-8")
    messages = []

    async def capture(message):
        messages.append(message)

    await app(scope, receive, capture)
    start = messages[0]
    if start["status"] != 200:
        for message in messages:
            await send(message)
        return
    await send(start)
    await send({"type": "http.response.body", "body": b"{", "more_body": True})
    raise ConnectionResetError


def serve(config_path: Path) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    sys.path.insert(0, config["wheel_root"])
    import uvicorn
    from pydantic import SecretStr

    from powercontext.builtin.persistence.sqlite import SQLiteConfig
    from powercontext.builtin.runtime.config import ExternalSkillsConfig, InferenceConfig
    from powercontext.server.factory import create_server_app
    from powercontext.server.settings import BearerAuthConfig, McpConfig, ServerLoggingConfig, ServerSettings

    settings = ServerSettings(
        workspace=Path(config["workspace"]),
        database=SQLiteConfig(url=config["database"]),
        inference=InferenceConfig(),
        external_skills=ExternalSkillsConfig(),
        auth=BearerAuthConfig(
            enabled=config["token"] is not None, token=SecretStr(config["token"]) if config["token"] else None
        ),
        mcp=McpConfig(enabled=False),
        logging=ServerLoggingConfig(level="CRITICAL", access=False),
    )
    app = create_server_app(settings=settings)

    async def proxy(scope, receive, send):
        if scope["type"] == "http":
            prefix = config["prefix"]
            if not scope["path"].startswith(prefix + "/"):
                await send({"type": "http.response.start", "status": 404, "headers": []})
                await send({"type": "http.response.body", "body": b""})
                return
            scope = dict(scope)
            scope["path"] = scope["path"][len(prefix) :]
            scope["raw_path"] = scope["path"].encode()
            scope["root_path"] = prefix
        await forward_with_response_loss(app, scope, receive, send, config.get("response_loss_path"))

    server = uvicorn.Server(
        uvicorn.Config(
            proxy,
            host="127.0.0.1",
            port=config["port"],
            log_level="critical",
            access_log=False,
            ssl_keyfile=config["key"] if config["tls"] else None,
            ssl_certfile=config["certificate"] if config["tls"] else None,
        )
    )

    def shutdown_on_input():
        sys.stdin.readline()
        server.should_exit = True

    threading.Thread(target=shutdown_on_input, daemon=True).start()

    async def run():
        nonlocal app
        if not config.get("provider"):
            await server.serve()
            return
        from powercontext.server.authz import PrincipalRef
        from powercontext.server.authz.composition import open_builtin_access_control
        from powercontext.server.settings import AccessControlConfig

        admin = PrincipalRef(type="service", id="desktop-fixture-admin")

        async with open_builtin_access_control(
            settings.database, bootstrap_administrators=(admin,), deployment_id="desktop-fixture"
        ) as access:
            app = create_server_app(
                settings=settings.model_copy(
                    update={"access": AccessControlConfig(mode="enforced", deployment_id="desktop-fixture")}
                ),
                authentication_provider=fixture_provider(config, admin),
                access_control=access,
            )
            await server.serve()

    asyncio.run(run())


def control_pipe(process: subprocess.Popen[bytes]) -> IO[bytes]:
    if process.stdin is None:
        process.kill()
        process.wait(timeout=10)
        raise HarnessFailure("missing_control_pipe")
    return process.stdin


def main() -> None:
    import httpx

    root = Path(__file__).resolve().parents[2]
    desktop = root / "desktop"
    artifact_dir = desktop / ".artifacts"
    wheels = list((artifact_dir / "server-wheel").glob("*.whl"))
    if len(wheels) != 1:
        raise HarnessFailure("wheel_count")
    wheel = wheels[0]
    executable = (
        desktop / "src-tauri/target/debug/examples" / ("server_probe.exe" if os.name == "nt" else "server_probe")
    )
    reports = []
    with tempfile.TemporaryDirectory(prefix="desktop-real-server-") as directory:
        temp = Path(directory)
        wheel_root = temp / "wheel"
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(wheel_root)
        subprocess.run([str(executable), "certificates", str(temp)], check=True, capture_output=True)
        for mode in ("loopback-anonymous", "loopback-bearer", "https-bearer-base-path", "loopback-provider"):
            case = temp / mode
            case.mkdir()
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            tls = mode.startswith("https")
            token = None if mode.endswith("anonymous") else secrets.token_urlsafe(32)
            prefix = "/proxy" if tls else ""
            endpoint = f"{'https' if tls else 'http'}://127.0.0.1:{port}{prefix}"
            config = {
                "response_loss_path": str(case / "response-loss-count"),
                "reader_token": secrets.token_urlsafe(32),
                "provider": mode == "loopback-provider",
                "identity_change_path": str(case / "identity-changed"),
                "wheel_root": str(wheel_root),
                "workspace": str(case),
                "database": f"sqlite+aiosqlite:///{case / 'data.db'}",
                "token": token,
                "prefix": prefix,
                "port": port,
                "tls": tls,
                "key": str(temp / "server-key.pem"),
                "certificate": str(temp / "server.pem"),
            }
            config_path = case / "server.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            allowed_env = {
                k: v
                for k, v in os.environ.items()
                if k.upper()
                in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "USERPROFILE", "APPDATA", "LOCALAPPDATA"}
            }
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            with (case / "server.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen(
                    [sys.executable, "-I", str(Path(__file__).resolve()), "--serve", str(config_path)],
                    stdin=subprocess.PIPE,
                    stdout=log,
                    stderr=log,
                    env=allowed_env,
                    creationflags=flags,
                )
                control = control_pipe(process)
                context = ssl.create_default_context(cafile=str(temp / "ca.pem")) if tls else True
                try:
                    with httpx.Client(
                        base_url=endpoint,
                        verify=context,
                        trust_env=False,
                        timeout=3,
                        headers={"Authorization": f"Bearer {token}"} if token else {},
                    ) as client:
                        for _ in range(100):
                            if process.poll() is not None:
                                raise HarnessFailure(
                                    "startup", (case / "server.log").read_text(encoding="utf-8")[-3000:]
                                )
                            try:
                                if client.get("/health/ready").status_code == 200:
                                    break
                            except httpx.HTTPError:
                                pass
                            time.sleep(0.2)
                        else:
                            raise HarnessFailure("readiness_timeout")
                        result = client.post(
                            "/v1/scopes",
                            json={
                                "title": "Desktop synthetic fixture",
                                "summary": "Dedicated no-model validation",
                                "idempotency_key": "desktop-fixture-scope",
                            },
                        )
                        result.raise_for_status()
                        scope_id = result.json()["scope_id"]
                        fixture = {
                            "response_loss_path": config["response_loss_path"],
                            "reader_token": config["reader_token"] if config["provider"] else None,
                            "identity_change_path": config["identity_change_path"] if config["provider"] else None,
                            "endpoint": endpoint,
                            "scope_id": scope_id,
                            "token": token,
                            "ca_pem": (temp / "ca.pem").read_text(encoding="utf-8") if tls else None,
                        }
                        fixture_path = case / "client.json"
                        fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
                        probe = subprocess.run(
                            [str(executable), str(fixture_path)],
                            capture_output=True,
                            text=True,
                            timeout=120,
                            creationflags=flags,
                        )
                        if probe.returncode:
                            raise HarnessFailure(mode, probe.stderr[-2000:])
                        client.get("/health/live").raise_for_status()
                        reports.append({
                            "mode": mode,
                            "result": "passed",
                            "serverAliveAfterClientExit": True,
                            "committedWriteWithLostResponseUnknownWithoutReplay": True,
                            "providerIdentityChangeInvalidatesContext": bool(config["provider"]),
                            "sameIdentityRevocationDeniesHistoricalCitation": bool(config["provider"]),
                            "sameTitleScopePagination51": bool(config["provider"]),
                            "performance": probe_measurement(probe.stdout),
                        })
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
    report = {
        "environment": {
            "os": platform.platform(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "clientBuildProfile": "debug",
            "percentileMethod": "nearest rank",
            "budget": "No approved numerical budget; observation only",
        },
        "serverWheel": wheel.name,
        "serverWheelSha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "contractSha256": hashlib.sha256(
            (root / "openapi/powercontext.yaml").read_text(encoding="utf-8").replace("\r\n", "\n").encode()
        ).hexdigest(),
        "desktopCommit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "desktopWorkingTreeDirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip()
        ),
        "networkScope": "Independent loopback HTTP/TLS fixture processes; not an external production deployment",
        "cases": reports,
    }
    (artifact_dir / "real-server.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--serve":
        serve(Path(sys.argv[2]))
    else:
        main()
