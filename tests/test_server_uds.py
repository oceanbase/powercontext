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

"""Configuration and filesystem ownership of the formal Unix listener."""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError
from typer.testing import CliRunner

from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.runtime.config import RuntimeConfig
from powercontext.cli.app import create_cli
from powercontext.server.cli import app
from powercontext.server.configuration import resolve_http_configuration, server_settings_context
from powercontext.server.settings import HttpConfig, ServerSettings
from powercontext.server.uds import UnixSocketError, unix_socket_listener

_UNIX = pytest.mark.skipif(sys.platform not in {"linux", "darwin"}, reason="UDS supports Linux and macOS")


def test_unsupported_platform_rejects_uds_without_affecting_tcp(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert HttpConfig().uds is None
    with pytest.raises(ValidationError, match="Linux and macOS"):
        HttpConfig(uds=Path("/run/private/core.sock"))


@_UNIX
@pytest.mark.parametrize("path", ["", "relative.sock", "/", "/run/../core.sock", "/run/a\0.sock", "/" + "界" * 35])
def test_invalid_uds_paths_fail_configuration(path):
    with pytest.raises(ValidationError):
        HttpConfig(uds=path)


@_UNIX
def test_uds_configuration_precedence_and_ignored_tcp_host(tmp_path, short_tmp_path, monkeypatch):
    config = tmp_path / "server.env"
    file_socket = short_tmp_path / "file.sock"
    env_socket = short_tmp_path / "env.sock"
    cli_socket = short_tmp_path / "cli.sock"
    config.write_text(f"POWERCONTEXT_SERVER_HTTP_UDS={file_socket}\n", encoding="utf-8")
    with server_settings_context(env_file=config, process_environment_overrides=True) as settings:
        assert settings.http.uds == file_socket
    monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP_UDS", str(env_socket))
    # No TCP listener exists in this mode, so an inert host must not require TCP's opt-in.
    monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP_HOST", "0.0.0.0")  # noqa: S104 - inert in UDS mode.
    with server_settings_context(env_file=config, process_environment_overrides=True) as settings:
        assert settings.http.uds == env_socket
    with server_settings_context(uds=cli_socket, env_file=config, process_environment_overrides=True) as settings:
        assert settings.http.uds == cli_socket
    assert not file_socket.exists() and not env_socket.exists() and not cli_socket.exists()


@_UNIX
@pytest.mark.parametrize("arguments", [["--host", "127.0.0.1"], ["--port", "8123"]])
def test_cli_rejects_tcp_flags_when_uds_is_selected(short_tmp_path, monkeypatch, arguments):
    monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP_UDS", str(short_tmp_path / "core.sock"))
    result = CliRunner().invoke(create_cli([app]), ["server", "run", "--no-env-file", *arguments])
    assert result.exit_code == 2
    assert "cannot be combined" in result.output
    assert not list(short_tmp_path.iterdir())


@_UNIX
def test_uds_requires_an_api_role(short_tmp_path):
    config = HttpConfig(uds=short_tmp_path / "core.sock")
    database = OceanBaseConfig(url=SecretStr("mysql+aoceanbase://user:password@localhost:2881/test?charset=utf8mb4"))
    assert ServerSettings(http=config, database=database, runtime=RuntimeConfig(artifact_processing_role="api"))
    with pytest.raises(ValidationError, match="role=background"):
        ServerSettings(http=config, database=database, runtime=RuntimeConfig(artifact_processing_role="background"))


@_UNIX
@pytest.mark.parametrize("unsafe", ["permissions", "symlink"])
def test_listener_refuses_unsafe_parent_without_changing_it(short_tmp_path, unsafe):
    directory = short_tmp_path / "run"
    target = short_tmp_path / "target"
    target.mkdir(mode=0o700)
    if unsafe == "symlink":
        directory.symlink_to(target, target_is_directory=True)
    else:
        directory.mkdir(mode=0o755)
    before = directory.lstat()
    with pytest.raises(UnixSocketError, match="directory"), unix_socket_listener(directory / "core.sock"):
        pytest.fail("Unsafe parent was accepted")
    assert directory.lstat().st_mode == before.st_mode
    assert not list(target.iterdir())


@_UNIX
@pytest.mark.parametrize("unsafe", ["symlink", "permissions", "hardlink"])
def test_listener_refuses_unsafe_lock_without_changing_it(short_tmp_path, unsafe):
    protected = short_tmp_path / "protected"
    protected.write_text("do not truncate", encoding="utf-8")
    protected.chmod(0o600)
    lock = short_tmp_path / "core.sock.lock"
    if unsafe == "symlink":
        lock.symlink_to(protected)
    elif unsafe == "hardlink":
        os.link(protected, lock)
    else:
        lock.write_text("do not truncate", encoding="utf-8")
        lock.chmod(0o644)
    before = lock.lstat()
    with pytest.raises(UnixSocketError), unix_socket_listener(short_tmp_path / "core.sock"):
        pytest.fail("Unsafe lock was accepted")
    assert lock.read_text(encoding="utf-8") == "do not truncate"
    assert lock.lstat().st_ino == before.st_ino
    assert protected.read_text(encoding="utf-8") == "do not truncate"


@_UNIX
def test_listener_cleanup_preserves_replacement_and_releases_lock(short_tmp_path):
    path = short_tmp_path / "core.sock"
    with unix_socket_listener(path) as listener:
        assert listener.family == socket.AF_UNIX
        path.unlink()
        path.write_text("replacement", encoding="utf-8")
    assert path.read_text(encoding="utf-8") == "replacement"
    path.unlink()
    with pytest.raises(RuntimeError, match="application failed"), unix_socket_listener(path):
        raise RuntimeError("application failed")  # noqa: TRY003
    assert not path.exists()
    with unix_socket_listener(path):
        assert path.is_socket()
    assert not path.exists()


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_external_scheme_requires_uds(scheme):
    with pytest.raises(ValidationError, match="HTTP_UDS_EXTERNAL_SCHEME requires HTTP_UDS"):
        HttpConfig(uds_external_scheme=scheme)


@_UNIX
@pytest.mark.parametrize("scheme", ["ftp", "HTTPS", ""])
def test_invalid_uds_external_scheme_is_rejected(short_tmp_path, scheme):
    with pytest.raises(ValidationError):
        HttpConfig(uds=short_tmp_path / "core.sock", uds_external_scheme=scheme)


@_UNIX
@pytest.mark.parametrize(
    ("document", "process", "expected_uds", "expected_port"),
    [
        (
            {
                "POWERCONTEXT_SERVER_HTTP": '{"uds":null,"port":8123}',
                "powercontext_server_http_uds": "/run/private/core.sock",
                "POWERCONTEXT_SERVER_HTTP_PORT": "8124",
            },
            {},
            "/run/private/core.sock",
            8124,
        ),
        (
            {"POWERCONTEXT_SERVER_HTTP_UDS": "/run/private/core.sock", "POWERCONTEXT_SERVER_HTTP_PORT": "8123"},
            {"POWERCONTEXT_SERVER_HTTP": '{"uds":null,"port":8124}'},
            None,
            8124,
        ),
        (
            {"POWERCONTEXT_SERVER_HTTP": '{"uds":"/run/private/core.sock","port":8123}'},
            {"POWERCONTEXT_SERVER_HTTP": '{"port":8124}'},
            "/run/private/core.sock",
            8124,
        ),
    ],
)
def test_http_detection_matches_server_source_precedence(
    tmp_path, monkeypatch, document, process, expected_uds, expected_port
):
    import shlex

    path = tmp_path / "server.env"
    path.write_text("".join(f"{key}={shlex.quote(value)}\n" for key, value in document.items()))
    for key, value in process.items():
        monkeypatch.setenv(key, value)
    listener = resolve_http_configuration(document, process)
    with server_settings_context(env_file=path, process_environment_overrides=True) as settings:
        assert listener == settings.http
    assert listener.uds == (Path(expected_uds) if expected_uds else None)
    assert listener.port == expected_port


@_UNIX
@pytest.mark.parametrize("started", [False, True])
def test_cli_checks_started_when_uvicorn_returns_normally(short_tmp_path, monkeypatch, started):
    """Older supported Uvicorn releases return normally after lifespan startup failure."""
    import uvicorn

    def returned_startup(server, sockets=None):
        server.started = started

    monkeypatch.setattr(uvicorn.Server, "run", returned_startup)
    path = short_tmp_path / "startup.sock"
    result = CliRunner().invoke(create_cli([app]), ["server", "run", "--no-env-file", "--uds", str(path)])
    assert result.exit_code == (0 if started else 3), result.output
    assert not path.exists()


@_UNIX
@pytest.mark.parametrize("deployment", ["none", "directory", "autostart"])
@pytest.mark.parametrize("public_url", [None, "https://proxy.example"])
def test_uds_source_deployment_requires_proxy_before_creating_state(tmp_path, short_tmp_path, deployment, public_url):
    from powercontext.server.factory import create_server_app
    from powercontext.server.settings import BearerAuthConfig, SourceManagementConfig
    from powercontext.source_management.models import ManagementError

    state = tmp_path / "deployment"
    settings = ServerSettings(
        http=HttpConfig(uds=short_tmp_path / "core.sock"),
        auth=BearerAuthConfig(enabled=True, token=SecretStr("uds-source-test-token")),
        source_plugins_dir=tmp_path / "plugins" if deployment == "directory" else None,
        source_management=SourceManagementConfig(autostart=deployment == "autostart", state_dir=state),
        public_url=public_url,
    )
    if deployment != "none" and public_url is None:
        with pytest.raises(ManagementError, match="POWERCONTEXT_SERVER_PUBLIC_URL"):
            create_server_app(settings=settings)
        assert not state.exists()
    else:
        assert create_server_app(settings=settings) is not None


@_UNIX
def test_uds_collect_load_requires_proxy_before_creating_state(tmp_path, short_tmp_path, monkeypatch):
    from powercontext.source_management.cli import source_app

    state = tmp_path / "deployment"
    monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP_UDS", str(short_tmp_path / "core.sock"))
    monkeypatch.setenv("POWERCONTEXT_SERVER_SOURCE_MANAGEMENT_AUTOSTART", "true")
    monkeypatch.setenv("POWERCONTEXT_SERVER_SOURCE_MANAGEMENT_STATE_DIR", str(state))
    config = tmp_path / "collect.yml"
    config.write_text("id: uds-collect\nplugin: unused.module\n", encoding="utf-8")

    result = CliRunner().invoke(source_app, ["load", "--config", str(config)])

    assert result.exit_code == 1, result.output
    assert "server-url-required" in result.output
    assert "POWERCONTEXT_SERVER_PUBLIC_URL" in result.output
    assert not state.exists()


@_UNIX
@pytest.mark.parametrize("endpoint", ["missing", "public_url", "argument"])
def test_uds_write_load_uses_only_explicit_proxy(tmp_path, short_tmp_path, monkeypatch, endpoint):
    import json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    from powercontext.source_management.cli import source_app

    requests = []

    class Proxy(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, body))
            payload = json.dumps({"id": body["id"]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):  # noqa: A002 - match the stdlib signature.
            pass

    config = tmp_path / "write.yml"
    config.write_text("id: uds-write\nentry: unused.module\n", encoding="utf-8")
    monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP_UDS", str(short_tmp_path / "core.sock"))
    # This server setting must not prevent a write-only command from selecting its own proxy.
    monkeypatch.setenv("POWERCONTEXT_SERVER_SOURCE_MANAGEMENT_AUTOSTART", "true")
    with ThreadingHTTPServer(("127.0.0.1", 0), Proxy) as proxy:
        url = f"http://127.0.0.1:{proxy.server_port}"
        monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP_PORT", str(proxy.server_port))
        arguments = ["load", "--kind", "write", "--config", str(config)]
        if endpoint == "public_url":
            monkeypatch.setenv("POWERCONTEXT_SERVER_PUBLIC_URL", url)
        elif endpoint == "argument":
            arguments.extend(["--server-url", url])
        thread = Thread(target=proxy.serve_forever, daemon=True)
        thread.start()
        try:
            result = CliRunner().invoke(source_app, arguments)
        finally:
            proxy.shutdown()
            thread.join(timeout=5)

    if endpoint == "missing":
        assert result.exit_code == 1, result.output
        assert "server-url-required" in result.output
        assert "--server-url or POWERCONTEXT_SERVER_PUBLIC_URL" in result.output
        assert requests == []
    else:
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == {"id": "uds-write"}
        assert requests == [("/v1/source-management/write/load", {"id": "uds-write", "entry": "unused.module"})]
