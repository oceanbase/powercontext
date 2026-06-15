import os
import socket
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock

import click
import pytest
from click.testing import CliRunner

from server.cli import server as server_cli


@pytest.fixture
def browser_capable_environment(monkeypatch):
    monkeypatch.setattr(server_cli, "dashboard_assets_available", lambda: True)
    monkeypatch.setattr(server_cli, "_is_containerized", lambda: False)
    monkeypatch.setattr(server_cli, "_has_graphical_environment", lambda: True)
    monkeypatch.setattr(server_cli, "_is_interactive_terminal", lambda: True)
    monkeypatch.delenv("CI", raising=False)
    for name in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("0.0.0.0", "http://127.0.0.1:8848/dashboard/"),
        ("127.0.0.1", "http://127.0.0.1:8848/dashboard/"),
        ("localhost", "http://localhost:8848/dashboard/"),
        ("::", "http://[::1]:8848/dashboard/"),
        ("2001:db8::1", "http://[2001:db8::1]:8848/dashboard/"),
    ],
)
def test_dashboard_url_uses_browser_reachable_host(host, expected):
    assert server_cli._dashboard_url(host, 8848) == expected


def test_should_open_browser_auto_and_explicit(browser_capable_environment):
    assert server_cli._should_open_browser(None) is True
    assert server_cli._should_open_browser(True) is True
    assert server_cli._should_open_browser(False) is False


@pytest.mark.parametrize("ssh_variable", ["SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"])
def test_should_open_browser_skips_ssh(
    monkeypatch, browser_capable_environment, ssh_variable
):
    monkeypatch.setenv(ssh_variable, "connected")

    assert server_cli._should_open_browser(True) is False


def test_should_open_browser_skips_ci(monkeypatch, browser_capable_environment):
    monkeypatch.setenv("CI", "true")

    assert server_cli._should_open_browser(True) is False


def test_should_open_browser_skips_container(monkeypatch, browser_capable_environment):
    monkeypatch.setattr(server_cli, "_is_containerized", lambda: True)

    assert server_cli._should_open_browser(True) is False


def test_should_open_browser_skips_missing_assets(
    monkeypatch, browser_capable_environment
):
    monkeypatch.setattr(server_cli, "dashboard_assets_available", lambda: False)

    assert server_cli._should_open_browser(True) is False


def test_should_open_browser_skips_headless_environment(
    monkeypatch, browser_capable_environment
):
    monkeypatch.setattr(server_cli, "_has_graphical_environment", lambda: False)

    assert server_cli._should_open_browser(True) is False


def test_explicit_open_bypasses_non_tty(monkeypatch, browser_capable_environment):
    monkeypatch.setattr(server_cli, "_is_interactive_terminal", lambda: False)

    assert server_cli._should_open_browser(None) is False
    assert server_cli._should_open_browser(True) is True


def test_waiter_opens_dashboard_once_when_ready(monkeypatch):
    ready = Mock(side_effect=[False, True])
    browser_open = Mock(return_value=True)
    monkeypatch.setattr(server_cli, "_dashboard_is_ready", ready)
    monkeypatch.setattr(server_cli.webbrowser, "open", browser_open)
    monkeypatch.setattr(server_cli.time, "sleep", lambda _seconds: None)

    server_cli._wait_and_open_dashboard(
        "http://127.0.0.1:8848/dashboard/",
        timeout=1,
        poll_interval=0,
    )

    assert ready.call_count == 2
    browser_open.assert_called_once_with("http://127.0.0.1:8848/dashboard/")


def test_dashboard_ready_requires_http_200(monkeypatch):
    response = MagicMock()
    response.status = 200
    response.__enter__.return_value = response
    opener = Mock(return_value=response)
    monkeypatch.setattr(server_cli._NO_PROXY_OPENER, "open", opener)

    assert server_cli._dashboard_is_ready("http://127.0.0.1:8848/dashboard/") is True
    opener.assert_called_once_with(
        "http://127.0.0.1:8848/dashboard/",
        timeout=1.0,
    )


def test_waiter_times_out_without_opening(monkeypatch):
    browser_open = Mock()
    warning = Mock()
    monotonic = Mock(side_effect=[0.0, 2.0])
    monkeypatch.setattr(server_cli.time, "monotonic", monotonic)
    monkeypatch.setattr(server_cli.webbrowser, "open", browser_open)
    monkeypatch.setattr(server_cli.logger, "warning", warning)

    server_cli._wait_and_open_dashboard(
        "http://127.0.0.1:8848/dashboard/",
        timeout=1,
        poll_interval=0,
    )

    browser_open.assert_not_called()
    warning.assert_called_once()


def test_waiter_handles_browser_error(monkeypatch):
    warning = Mock()
    monkeypatch.setattr(server_cli, "_dashboard_is_ready", lambda _url: True)
    monkeypatch.setattr(
        server_cli.webbrowser, "open", Mock(side_effect=RuntimeError("browser failed"))
    )
    monkeypatch.setattr(server_cli.logger, "warning", warning)

    server_cli._wait_and_open_dashboard("http://127.0.0.1:8848/dashboard/")

    warning.assert_called_once()


def test_start_browser_skips_existing_dashboard(monkeypatch):
    thread = Mock()
    monkeypatch.setattr(server_cli, "_dashboard_is_ready", lambda _url: True)
    monkeypatch.setattr(server_cli.threading, "Thread", thread)

    server_cli._start_dashboard_browser("0.0.0.0", 8848)

    thread.assert_not_called()


def test_start_browser_creates_one_daemon_waiter(monkeypatch):
    thread = Mock()
    thread.return_value.start = Mock()
    monkeypatch.setattr(server_cli, "_dashboard_is_ready", lambda _url: False)
    monkeypatch.setattr(server_cli.threading, "Thread", thread)

    server_cli._start_dashboard_browser("0.0.0.0", 8848)

    thread.assert_called_once_with(
        target=server_cli._wait_and_open_dashboard,
        args=("http://127.0.0.1:8848/dashboard/",),
        name="powermem-dashboard-browser",
        daemon=True,
    )
    thread.return_value.start.assert_called_once_with()


def test_assert_bind_available_rejects_occupied_port():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]

    try:
        with pytest.raises(click.ClickException) as exc_info:
            server_cli._assert_bind_available("127.0.0.1", port)
    finally:
        listener.close()

    assert f"Port {port} is already in use on 127.0.0.1" in str(exc_info.value)
    assert "choose another --port" in str(exc_info.value)


def test_cli_starts_one_browser_waiter_with_reload_and_workers(monkeypatch):
    runner = CliRunner()
    bind_available = Mock()
    start_browser = Mock()
    uvicorn_run = Mock()
    original = {
        "host": server_cli.config.host,
        "port": server_cli.config.port,
        "workers": server_cli.config.workers,
        "reload": server_cli.config.reload,
    }
    monkeypatch.setattr(server_cli, "_should_open_browser", lambda _requested: True)
    monkeypatch.setattr(server_cli, "_assert_bind_available", bind_available)
    monkeypatch.setattr(server_cli, "_start_dashboard_browser", start_browser)
    monkeypatch.setattr(server_cli, "_is_embedded_storage", lambda: False)
    monkeypatch.setattr(server_cli, "_setup_server_logging", lambda: None)
    monkeypatch.setattr(server_cli, "_run_server_app", uvicorn_run)

    try:
        result = runner.invoke(
            server_cli.server,
            [
                "--host",
                "0.0.0.0",
                "--port",
                "9988",
                "--workers",
                "3",
                "--reload",
                "--open-browser",
            ],
        )
    finally:
        for name, value in original.items():
            setattr(server_cli.config, name, value)

    assert result.exit_code == 0
    bind_available.assert_called_once_with("0.0.0.0", 9988)
    start_browser.assert_called_once_with("0.0.0.0", 9988)
    uvicorn_run.assert_called_once()
    assert uvicorn_run.call_args.kwargs["workers"] == 1


def test_cli_no_open_browser_disables_waiter(monkeypatch):
    runner = CliRunner()
    bind_available = Mock()
    should_open = Mock(return_value=False)
    start_browser = Mock()
    monkeypatch.setattr(server_cli, "_assert_bind_available", bind_available)
    monkeypatch.setattr(server_cli, "_should_open_browser", should_open)
    monkeypatch.setattr(server_cli, "_start_dashboard_browser", start_browser)
    monkeypatch.setattr(server_cli, "_is_embedded_storage", lambda: False)
    monkeypatch.setattr(server_cli, "_setup_server_logging", lambda: None)
    monkeypatch.setattr(server_cli, "_run_server_app", Mock())

    result = runner.invoke(server_cli.server, ["--no-open-browser"])

    assert result.exit_code == 0
    bind_available.assert_called_once_with(
        server_cli.config.host, server_cli.config.port
    )
    should_open.assert_called_once_with(False)
    start_browser.assert_not_called()


def test_cli_port_in_use_stops_before_browser_and_uvicorn(monkeypatch):
    runner = CliRunner()
    should_open = Mock(return_value=True)
    start_browser = Mock()
    uvicorn_run = Mock()
    monkeypatch.setattr(
        server_cli,
        "_assert_bind_available",
        Mock(
            side_effect=click.ClickException(
                "Port 8848 is already in use on 127.0.0.1. "
                "Stop the existing process or choose another --port."
            )
        ),
    )
    monkeypatch.setattr(server_cli, "_should_open_browser", should_open)
    monkeypatch.setattr(server_cli, "_start_dashboard_browser", start_browser)
    monkeypatch.setattr(server_cli, "_is_embedded_storage", lambda: False)
    monkeypatch.setattr(server_cli, "_setup_server_logging", lambda: None)
    monkeypatch.setattr(server_cli, "_run_server_app", uvicorn_run)

    result = runner.invoke(
        server_cli.server,
        ["--host", "127.0.0.1", "--port", "8848", "--open-browser"],
    )

    assert result.exit_code != 0
    assert "Port 8848 is already in use on 127.0.0.1" in result.output
    should_open.assert_not_called()
    start_browser.assert_not_called()
    uvicorn_run.assert_not_called()


def test_cli_help_documents_browser_options():
    result = CliRunner().invoke(server_cli.server, ["--help"])

    assert result.exit_code == 0
    assert "--open-browser / --no-open-browser" in result.output


def test_cli_module_import_does_not_require_server_extras():
    src_path = str(Path(__file__).resolve().parents[3] / "src")
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        src_path
        if not env.get("PYTHONPATH")
        else f"{src_path}{os.pathsep}{env['PYTHONPATH']}"
    )
    script = (
        "import sys; "
        "sys.modules['fastapi'] = None; "
        "sys.modules['uvicorn'] = None; "
        "import server.cli.server"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
