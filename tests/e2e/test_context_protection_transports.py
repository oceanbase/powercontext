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

"""One public prepare/check contract through ASGI HTTP and separate UDS / HTTPS Servers."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import ssl
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from ipaddress import ip_address
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from pydantic import SecretStr

from powercontext.builtin.inference.privacy import InferenceProtectionConfig, UpstreamProfile
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig, RuntimeConfig
from powercontext.client import PowerContextClient
from powercontext.http._generated.models import CheckContextRequest, PrepareContextRequest, PreparedContextWithReuse
from powercontext.server.authentication import (
    AuthenticationRejectedError,
    AuthenticationResult,
    ProviderReadiness,
)
from powercontext.server.authz import PrincipalRef
from powercontext.server.authz.composition import open_builtin_access_control
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, McpConfig, MetricsConfig, ServerSettings
from tests.e2e.server_storage import prepare_server_database

_FIXTURE_PATH = Path(__file__).resolve()
_ADMIN = PrincipalRef(type="service", id="transport-admin")
_OTHER = PrincipalRef(type="user", id="transport-other")


class _Authentication:
    async def authenticate(self, request):
        principals = {"Bearer transport-admin": _ADMIN, "Bearer transport-other": _OTHER}
        principal = principals.get(request.headers.get("authorization"))
        if principal is None:
            raise AuthenticationRejectedError
        return AuthenticationResult(subject=principal, credential_id=principal.id)

    async def readiness(self):
        return ProviderReadiness(ready=True)


def _settings(directory: Path) -> ServerSettings:
    return ServerSettings(
        workspace=directory,
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{directory / 'runtime.db'}"),
        runtime=RuntimeConfig(artifact_processing_families=()),
        access=AccessControlConfig(mode="enforced", deployment_id="context-transports"),
        mcp=McpConfig(enabled=False),
        metrics=MetricsConfig(enabled=False),
        cursor_signing_secret=SecretStr("disposable-transport-test-secret-32-bytes"),
        inference=InferenceConfig(
            protection=InferenceProtectionConfig(
                mode="enforcing",
                forbidden_values=(SecretStr("synthetic-transport-secret"),),
                targets={
                    "model": UpstreamProfile(
                        endpoint="https://model.example/v1/chat/completions",
                        model="test",
                        protocol="openai-chat",
                        context_window_tokens=65536,
                        max_output_tokens=64,
                        validated_utf8_token_bound=True,
                    )
                },
            )
        ),
    )


def _app(directory: Path):
    return create_server_app(
        settings=_settings(directory),
        authentication_provider=_Authentication(),
        scheduler_path=directory / "scheduler.db",
    )


def _tls_port(directory: Path) -> int:
    """Issue a local CA / localhost certificate; clients trust this CA explicitly."""
    now = datetime.now(UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Disposable PowerContext test CA")])
    ca = (
        x509
        .CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    certificate = (
        x509
        .CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(ca.subject)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ip_address("127.0.0.1")),
            ]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    (directory / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (directory / "server.pem").write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path = directory / "server-key.pem"
    key_path.write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        return reserved.getsockname()[1]


def _serve(directory: str, listener: str, kind: str) -> None:
    app = _app(Path(directory))
    if kind == "https":
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=int(listener),
            ssl_certfile=str(Path(directory) / "server.pem"),
            ssl_keyfile=str(Path(directory) / "server-key.pem"),
            log_level="warning",
            access_log=False,
        )
    else:
        # The UDS profile never allocates a TCP listener.
        uvicorn.run(app, uds=listener, log_level="warning", access_log=False)


@asynccontextmanager
async def _process_server(directory: Path, socket_path: Path, tls_port: int | None):
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("POWERCONTEXT_SERVER_", "OTEL_", "OPENAI_"))
    }
    environment["PYTHONPATH"] = str(_FIXTURE_PATH.parents[2])
    # Import this fixture by absolute path while keeping the child working directory disposable.
    runner = "import runpy,sys; runpy.run_path(sys.argv[1])['_serve'](sys.argv[2], sys.argv[3], sys.argv[4])"
    log_path = directory / "server-transport.log"
    listener, kind = (str(socket_path), "uds") if tls_port is None else (str(tls_port), "https")
    with log_path.open("w+", encoding="utf-8") as output:
        process = await asyncio.to_thread(
            subprocess.Popen,
            [sys.executable, "-c", runner, str(_FIXTURE_PATH), str(directory), listener, kind],
            cwd=directory,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            transport = (
                httpx.AsyncHTTPTransport(uds=str(socket_path))
                if tls_port is None
                else httpx.AsyncHTTPTransport(verify=ssl.create_default_context(cafile=directory / "ca.pem"))
            )
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://localhost" if tls_port is None else f"https://localhost:{tls_port}",
                timeout=5,
                trust_env=False,
                headers={"Authorization": "Bearer transport-admin"},
            ) as http:
                deadline = time.monotonic() + 25
                last_error = "No response"
                while True:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        pytest.fail(f"{kind} Server did not start: {last_error}; {log_path.read_text()}")
                    try:
                        response = await http.get("/health/ready")
                        if response.status_code == 200:
                            break
                    except httpx.ConnectError as error:
                        last_error = str(error)
                    await asyncio.sleep(0.05)
                yield http
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
                try:
                    await asyncio.to_thread(process.wait, timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait, timeout=5)


def _send(scope_id: str, envelope: PreparedContextWithReuse) -> dict[str, Any]:
    content = envelope.context.content
    assert content is not None
    prefix = "当前任务: 继续实现。\n"
    body = (
        json.dumps(
            {
                "model": "test",
                "max_tokens": 64,
                "messages": [{"role": "user", "content": prefix + content + "\n回答时保留端口。"}],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    return {
        "scope_id": scope_id,
        "phase": "before_send",
        "target_profile": "model",
        "payload": {
            "schema": "powercontext.model-json-payload.v1",
            "headers": {"content-type": "application/json"},
            "body": body,
        },
        "prepared_contexts": [
            {
                "binding": envelope.reuse.binding,
                "location": {
                    "schema": "powercontext.model-json-text-location.v1",
                    "pointer": "/messages/0/content",
                    "start_byte": len(prefix.encode()),
                    "end_byte": len((prefix + content).encode()),
                },
            }
        ],
    }


def _release(scope_id: str, binding: str) -> dict[str, Any]:
    return {
        "scope_id": scope_id,
        "phase": "before_release",
        "target_profile": "model",
        "request_binding": binding,
        "completion": "complete",
        "payload": {
            "schema": "powercontext.model-json-payload.v1",
            "headers": {"content-type": "application/json"},
            "body": json.dumps(
                {
                    "model": "test",
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "继续使用端口 8123。"},
                            "finish_reason": "stop",
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
        },
    }


async def _create_scope(http: httpx.AsyncClient, name: str) -> str:
    response = await http.post(
        "/v1/scopes",
        json={
            "title": name,
            "summary": "Disposable UDS contract test",
            "idempotency_key": name,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["scope_id"]


@pytest.mark.skipif(sys.platform == "win32", reason="Requires a Unix domain socket and POSIX process shutdown")
@pytest.mark.parametrize("transport_kind", ["uds", "https"])
def test_prepare_check_preserve_bytes_and_authority_over_real_transport(tmp_path, short_tmp_path, transport_kind):
    tls_port = _tls_port(tmp_path) if transport_kind == "https" else None

    async def scenario():
        settings = _settings(tmp_path)
        await asyncio.to_thread(prepare_server_database, settings)
        async with open_builtin_access_control(
            settings.database,
            bootstrap_administrators=(_ADMIN, _OTHER),
            deployment_id="context-transports",
        ):
            pass
        app = _app(tmp_path)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost",
                headers={"Authorization": "Bearer transport-admin"},
            ) as http,
        ):
            scope_id = await _create_scope(http, "transport-scope")
            other_scope = await _create_scope(http, "other-scope")
            for current_scope in (scope_id, other_scope):
                for principal in (_ADMIN, _OTHER):
                    granted = await http.post(
                        "/v1/access/bindings/create",
                        json={
                            "subject": {"type": principal.type, "id": principal.id},
                            "resource": {"type": "scope", "scope_id": current_scope},
                            "role": "scope.contributor",
                            "idempotency_key": f"{current_scope}-{principal.id}",
                        },
                    )
                    assert granted.status_code == 201, granted.text
            remembered = await http.post(
                "/v1/memory/remember",
                json={
                    "scope_id": scope_id,
                    "kind": "fact",
                    "text": "Alpha 中文网关必须保留端口 8123。",
                },
            )
            assert remembered.status_code == 200, remembered.text
            prepare = PrepareContextRequest.model_validate({
                "scope_id": scope_id,
                "query": "alpha",
                "include_reuse": True,
                "assembly": {"sections": [{"family": "memory", "limit": 8}]},
            })
            client = PowerContextClient(str(http.base_url), token=_ADMIN.id, http_client=http)
            envelope = await client.prepare_context(prepare)
            assert isinstance(envelope, PreparedContextWithReuse)
            send = _send(scope_id, envelope)
            before_http = await client.check_context(CheckContextRequest.model_validate(send))
            assert before_http.decision == "allow" and before_http.coverage.complete
            assert before_http.binding is not None
            release = _release(scope_id, before_http.binding)
            after_http = await client.check_context(CheckContextRequest.model_validate(release))
            assert after_http.decision == "allow"
            codec = app.state.application.context_binding_codec
            claims = codec.open(envelope.reuse.binding, purpose="context-reuse", now=datetime.now(UTC))
            expired = codec.seal(claims, purpose="context-reuse", expires_at=datetime.now(UTC) - timedelta(seconds=1))

        async with _process_server(tmp_path, short_tmp_path / "context.sock", tls_port) as http:
            assert (await http.get("/v1/capabilities", headers={"Authorization": ""})).status_code == 401
            assert (await http.get("/v1/capabilities", headers={"Authorization": "Bearer invalid"})).status_code == 401
            client = PowerContextClient(str(http.base_url), token=_ADMIN.id, http_client=http)
            remote_envelope = await client.prepare_context(prepare)
            assert isinstance(remote_envelope, PreparedContextWithReuse)
            assert remote_envelope.context == envelope.context
            # Reuse the previous process's exact envelope, proving stable Server deployment/key binding.
            before_remote = await client.check_context(CheckContextRequest.model_validate(send))
            assert before_remote.decision == before_http.decision == "allow"
            assert before_remote.coverage == before_http.coverage
            assert before_remote.policy_version == before_http.policy_version
            assert before_remote.binding is not None
            checked_claims = codec.open(
                before_remote.binding,
                purpose="context-check.before_send",
                now=datetime.now(UTC),
            )
            canonical_headers = json.dumps(sorted(send["payload"]["headers"].items()), separators=(",", ":"))
            expected_digest = sha256(canonical_headers.encode() + b"\0" + send["payload"]["body"].encode()).hexdigest()
            assert checked_claims["payload_digest"] == expected_digest
            block = envelope.context.content
            assert block is not None
            block_bytes = len(block.encode())
            multi = json.loads(json.dumps(send))
            multi_body = json.loads(multi["payload"]["body"])
            multi_body["messages"][0]["content"] = block + "\n" + block
            multi["payload"]["body"] = json.dumps(multi_body, ensure_ascii=False)
            multi["prepared_contexts"] = [
                {
                    "binding": envelope.reuse.binding,
                    "location": {
                        "schema": "powercontext.model-json-text-location.v1",
                        "pointer": "/messages/0/content",
                        "start_byte": start,
                        "end_byte": start + block_bytes,
                    },
                }
                for start in (0, block_bytes + 1)
            ]
            allowed_multi = await http.post("/v1/context/check", json=multi)
            assert allowed_multi.status_code == 200 and allowed_multi.json()["decision"] == "allow"
            for locations in (multi["prepared_contexts"][:1], list(reversed(multi["prepared_contexts"]))):
                rejected_multi = await http.post(
                    "/v1/context/check",
                    json=multi | {"prepared_contexts": locations},
                )
                assert rejected_multi.status_code == 422, rejected_multi.text
            after_remote = await client.check_context(CheckContextRequest.model_validate(release))
            assert after_remote.decision == after_http.decision == "allow"
            assert after_remote.coverage == after_http.coverage
            for payload, headers in (
                (send, {"Authorization": "Bearer transport-other"}),
                (send | {"scope_id": other_scope}, None),
                (release, {"Authorization": "Bearer transport-other"}),
                (release | {"scope_id": other_scope}, None),
                (send | {"prepared_contexts": [send["prepared_contexts"][0] | {"binding": expired}]}, None),
            ):
                refused = await http.post("/v1/context/check", json=payload, headers=headers)
                assert refused.status_code == 200, refused.text
                assert refused.json()["decision"] == "withhold"
                assert "binding" not in refused.json()
                assert refused.json()["reason_codes"] == ["context-refresh-required"]
            missing = await http.post("/v1/context/check", json=send | {"prepared_contexts": []})
            assert missing.status_code == 422, missing.text

    asyncio.run(scenario())
