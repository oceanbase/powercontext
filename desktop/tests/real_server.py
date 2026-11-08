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

import argparse
import asyncio
import hashlib
import io
import json
import os
import platform
import re
import secrets
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import IO, TextIO


class HarnessFailure(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"Desktop isolated fixture failed ({code}): {detail}")


def probe_measurement(
    output: str, product_profile_id: str | None = None, *, require_scoped_authorization: bool = False
) -> dict[str, object]:
    measurement = json.loads(output)
    if measurement.get("result") != "passed":
        raise HarnessFailure("native_probe_result")
    if product_profile_id is not None and (
        measurement.get("qualificationSource") != "bundled product records"
        or measurement.get("qualificationId") != product_profile_id
    ):
        raise HarnessFailure("product_probe_qualification")
    if require_scoped_authorization and measurement.get("scopedAuthorizationWithoutObservation") is not True:
        raise HarnessFailure("scoped_authorization_probe_required")
    return measurement["performance"]


def fixture_provider(config, admin):
    from powercontext.builtin.authz import PrincipalRef
    from powercontext.server.authentication import (
        AuthenticationRejectedError,
        AuthenticationResult,
        ProviderReadiness,
    )

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


async def forward_with_response_gate(app, scope, receive, send, gate):
    """Delay only the response to a committed write, controlled by the UI harness."""
    messages = []

    async def capture(message):
        messages.append(message)

    await app(scope, receive, capture)

    def wait_for_release():
        Path(gate + ".ready").touch()
        deadline = time.monotonic() + 12
        while Path(gate).exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        if Path(gate).exists():
            raise HarnessFailure("installed_response_gate_timeout")

    await asyncio.to_thread(wait_for_release)
    for message in messages:
        await send(message)


async def forward_with_statistics_usage(app, scope, receive, send, control_path):
    """Seed a partial provider report through the real Runtime's usage recorder.

    This is explicit fixture input, not evidence of an actual provider call.
    The HTTP stats response, aggregation, persistence and native parsing are real.
    """
    control = Path(control_path) if control_path else None
    if scope["type"] != "http" or scope["path"] != "/v1/stats" or control is None or not control.exists():
        await app(scope, receive, send)
        return
    from powercontext.builtin.inference import InferenceUsage
    from powercontext.builtin.statistics import ModelUsageOperation, ModelUsagePurpose

    messages = []
    while True:
        message = await receive()
        messages.append(message)
        if not message.get("more_body", False):
            break
    request = json.loads(b"".join(message.get("body", b"") for message in messages))
    scope_id = request["selection"]["scope_ids"][0]
    await app.state.application.statistics.for_scope(scope_id).record_model_usage(
        ModelUsagePurpose.MEMORY_RECALL,
        ModelUsageOperation.GENERATION,
        InferenceUsage(requests=2, input_tokens=None, output_tokens=9),
    )
    control.unlink()

    async def replay():
        return messages.pop(0) if messages else await receive()

    await app(scope, replay, send)


def measured_requests(app):
    """Observe synthetic HTTP completion without recording request content."""
    request_number = 0

    async def measured_proxy(scope, receive, send):
        nonlocal request_number
        if scope["type"] != "http":
            await app(scope, receive, send)
            return
        request_number += 1
        number = request_number
        started = time.perf_counter()
        status = None
        complete = False
        print(json.dumps({"request": number, "phase": "start", "method": scope["method"]}), flush=True)

        async def observed_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await app(scope, receive, observed_send)
            complete = True
        finally:
            print(
                json.dumps({
                    "request": number,
                    "phase": "finish",
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    "status": status,
                    "complete": complete,
                }),
                flush=True,
            )

    return measured_proxy


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
        gate = config.get("response_gate_path")
        if gate and scope["type"] == "http" and scope["path"] == "/v1/memory/remember":
            await forward_with_response_gate(app, scope, receive, send, gate)
            return
        if config.get("statistics_usage_path") and scope["type"] == "http" and scope["path"] == "/v1/stats":
            await forward_with_statistics_usage(app, scope, receive, send, config["statistics_usage_path"])
            return
        await forward_with_response_loss(app, scope, receive, send, config.get("response_loss_path"))

    server = uvicorn.Server(
        uvicorn.Config(
            measured_requests(proxy),
            host="127.0.0.1",
            port=config["port"],
            log_level=config.get("uvicorn_log_level", "critical"),
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
        from powercontext.builtin.authz import PrincipalRef
        from powercontext.builtin.authz.composition import open_builtin_access_control
        from powercontext.builtin.persistence.migrations.server_readiness import prepare_server_storage
        from powercontext.server.settings import AccessControlConfig

        admin = PrincipalRef(type="service", id="desktop-fixture-admin")

        # Bootstrap the complete Server before the separate Access context opens it.
        await asyncio.to_thread(prepare_server_storage, settings)
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


@contextmanager
def retained_fixture_server(
    process: subprocess.Popen[bytes],
    log: TextIO,
    log_path: Path,
    artifact_dir: Path,
    *,
    label: str,
    port: int,
    wheel_sha256: str,
) -> Iterator[None]:
    """Retain bounded synthetic diagnostics before either harness removes storage."""
    control = control_pipe(process)
    report = {
        "pid": process.pid,
        "port": port,
        "wheel_sha256": wheel_sha256,
        "outcome": "failed",
        "forced_kill": False,
    }
    try:
        yield
        report["outcome"] = "passed"
    except BaseException as error:
        report["exception_type"] = type(error).__name__
        error.add_note(f"Fixture {label} Server pid={process.pid}, port={port}")
        raise
    finally:
        report["before_cleanup_returncode"] = process.poll()
        try:
            if process.poll() is None:
                with suppress(BrokenPipeError):
                    control.write(b"stop\n")
                    control.flush()
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    report["forced_kill"] = True
                    process.kill()
                    process.wait(timeout=10)
        finally:
            report["after_cleanup_returncode"] = process.poll()
            with suppress(BrokenPipeError):
                control.close()
            # Databases, credentials and configuration stay in disposable storage.
            try:
                log.flush()
                with log_path.open("rb") as source:
                    size = source.seek(0, os.SEEK_END)
                    source.seek(max(0, size - 256 * 1024))
                    tail = source.read(256 * 1024)
                report["log_bytes"] = size
                report["log_truncated"] = size > len(tail)
                retained = Path(tempfile.mkdtemp(prefix=label + "-", dir=artifact_dir))
                (retained / "server.log").write_bytes(tail)
                (retained / "process.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            except OSError:
                print("Fixture Server diagnostics could not be retained.", file=sys.stderr)


def catalog_profile(desktop: Path, profile_id: str):
    catalog = json.loads((desktop / "src-tauri/src/connections/compatibility.json").read_text(encoding="utf-8"))
    profile = next((profile for profile in catalog if profile["id"] == profile_id), None)
    if profile is None:
        raise HarnessFailure("product_qualification_missing")
    return profile


def product_profile(desktop: Path, profile_id: str, wheel: Path | None = None):
    """Verify the actual archive before extraction, subprocesses, or product evidence."""
    if wheel is None:
        wheels = list((desktop / ".artifacts/server-wheel").glob("*.whl"))
        if len(wheels) != 1:
            raise HarnessFailure("wheel_count")
        wheel = wheels[0]
    profile = catalog_profile(desktop, profile_id)
    contract = json.loads((desktop / "src-tauri/src/transport/operations.json").read_text(encoding="utf-8"))
    if (
        hashlib.sha256(wheel.read_bytes()).hexdigest() != profile["artifactSha256"]
        or profile["contractSha256"] != contract["contractSha256"]
        or not set(contract["operations"]).issubset(profile["operations"])
    ):
        raise HarnessFailure("product_qualification_mismatch")
    return profile


def rebuilt_profile(desktop: Path, profile_id: str, wheel: Path):
    """Build distinct fixture evidence only after comparing every runtime file to Git."""
    original = catalog_profile(desktop, profile_id)
    commit = original["serverCommit"]
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise HarnessFailure("rebuilt_source_required")
    source = subprocess.check_output(
        ["git", "-c", "core.autocrlf=false", "archive", "--format=zip", commit], cwd=desktop.parent
    )
    prefixes = ("powercontext/", "powercontext_service_bootstrap/")
    with zipfile.ZipFile(io.BytesIO(source)) as archive, zipfile.ZipFile(wheel) as built:
        expected = {
            name[4:]: archive.read(name).replace(b"\r\n", b"\n")
            for name in archive.namelist()
            if name.startswith(tuple("src/" + prefix for prefix in prefixes)) and not name.endswith("/")
        }
        actual = {
            name: built.read(name).replace(b"\r\n", b"\n")
            for name in built.namelist()
            if name.startswith(prefixes) and not name.endswith("/")
        }
    if not expected or expected != actual:
        raise HarnessFailure("rebuilt_source_mismatch")
    contract = json.loads((desktop / "src-tauri/src/transport/operations.json").read_text(encoding="utf-8"))
    return {
        **original,
        "id": "rebuilt-" + original["id"],
        "artifactSha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "contractSha256": contract["contractSha256"],
        "evidence": "Explicit rebuilt-source fixture; not exact product-artifact qualification",
    }


def selected_qualification(desktop: Path, wheel: Path, product: str | None, rebuilt: str | None):
    from fixture_qualification import fixture_profile

    if product:
        return product_profile(desktop, product, wheel)
    if rebuilt:
        return rebuilt_profile(desktop, rebuilt, wheel)
    profile = fixture_profile()
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != profile["artifactSha256"]:
        raise HarnessFailure("fixture_qualification_mismatch")
    return profile


def main(
    *,
    product_qualification: str | None = None,
    wheel_path: Path | None = None,
    rebuilt_qualification: str | None = None,
) -> None:
    import httpx

    root = Path(__file__).resolve().parents[2]
    desktop = root / "desktop"
    artifact_dir = desktop / ".artifacts"
    wheels = [wheel_path] if wheel_path else list((artifact_dir / "server-wheel").glob("*.whl"))
    if len(wheels) != 1:
        raise HarnessFailure("wheel_count")
    wheel = wheels[0]
    wheel_bytes = wheel.read_bytes()
    compatibility = selected_qualification(desktop, wheel, product_qualification, rebuilt_qualification)
    wheel_sha256 = hashlib.sha256(wheel_bytes).hexdigest()
    if wheel_sha256 != compatibility["artifactSha256"]:
        raise HarnessFailure("qualification_artifact_changed")
    authenticated_legacy = "get_connection_identity" not in compatibility["operations"]
    executable = (
        desktop / "src-tauri/target/debug/examples" / ("server_probe.exe" if os.name == "nt" else "server_probe")
    )
    reports = []
    with tempfile.TemporaryDirectory(prefix="desktop-real-server-") as directory:
        temp = Path(directory)
        wheel_root = temp / "wheel"
        with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as archive:
            archive.extractall(wheel_root)
        subprocess.run([str(executable), "certificates", str(temp)], check=True, capture_output=True)
        for mode in ("loopback-anonymous", "loopback-bearer", "https-bearer-base-path", "loopback-provider"):
            if authenticated_legacy and mode == "loopback-anonymous":
                continue
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
                "statistics_usage_path": str(case / "statistics-partial-usage"),
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
                "uvicorn_log_level": "warning",
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
                context = ssl.create_default_context(cafile=str(temp / "ca.pem")) if tls else True
                with (
                    retained_fixture_server(
                        process,
                        log,
                        case / "server.log",
                        artifact_dir,
                        label=f"real-server-{mode}",
                        port=port,
                        wheel_sha256=wheel_sha256,
                    ),
                    httpx.Client(
                        base_url=endpoint,
                        verify=context,
                        trust_env=False,
                        timeout=3,
                        headers={"Authorization": f"Bearer {token}"} if token else {},
                    ) as client,
                ):
                    for _ in range(100):
                        if process.poll() is not None:
                            raise HarnessFailure("startup", (case / "server.log").read_text(encoding="utf-8")[-3000:])
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
                        "compatibility_profile": compatibility,
                        "product_qualification": bool(product_qualification),
                        "response_loss_path": config["response_loss_path"],
                        "statistics_usage_path": config["statistics_usage_path"],
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
                        "sameIdentityRevocationDeniesExactMemoryRead": bool(config["provider"]),
                        "sameTitleScopePagination51": bool(config["provider"]),
                        "scopedNativeAuthorizationWithoutServerObservation": config["provider"]
                        and not authenticated_legacy,
                        "performance": probe_measurement(
                            probe.stdout,
                            product_qualification,
                            require_scoped_authorization=config["provider"] and not authenticated_legacy,
                        ),
                    })
    report = {
        "qualificationMode": (
            "bundled-product"
            if product_qualification
            else "rebuilt-source-fixture"
            if rebuilt_qualification
            else "disposable-fixture"
        ),
        "qualificationReference": product_qualification or rebuilt_qualification,
        "environment": {
            "os": platform.platform(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "clientBuildProfile": "debug",
            "percentileMethod": "nearest rank",
            "budget": "No approved numerical budget; observation only",
        },
        "qualificationId": compatibility["id"],
        "qualificationSource": "bundled product records" if product_qualification else "explicit fixture",
        "serverWheel": wheel.name,
        "serverWheelSha256": wheel_sha256,
        "contractSha256": compatibility["contractSha256"],
        "clientContractSha256": hashlib.sha256(
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
        parser = argparse.ArgumentParser(description=__doc__)
        qualification = parser.add_mutually_exclusive_group()
        qualification.add_argument("--product-qualification", help="Require an exact bundled product artifact")
        qualification.add_argument(
            "--rebuilt-qualification", help="Verify source equivalence and report distinct fixture evidence"
        )
        parser.add_argument("--wheel", type=Path)
        args = parser.parse_args()
        main(
            product_qualification=args.product_qualification,
            wheel_path=args.wheel,
            rebuilt_qualification=args.rebuilt_qualification,
        )
