"""
CLI command for starting the PowerMem API server
"""

import errno
import logging
import os
import platform
import socket
import sys as _sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from typing import Optional


def _require_packages(packages: tuple[str, ...], install_extra: str) -> None:
    missing = []
    for pkg in packages:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        _sys.stderr.write(
            f"Missing dependencies: {', '.join(missing)}.\n"
            f"Run: pip install 'powermem[{install_extra}]'\n"
        )
        _sys.exit(1)


def _require_server_deps() -> None:
    _require_packages(("fastapi", "uvicorn"), "server")


_require_packages(("click",), "server")

import click

from ..config import config
from ..dashboard_assets import dashboard_assets_available


logger = logging.getLogger("server")
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_WINDOWS_ADDRESS_IN_USE = 10048


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _is_containerized() -> bool:
    """Return whether the process appears to run inside a container."""
    return (
        os.path.exists("/.dockerenv")
        or os.path.exists("/run/.containerenv")
        or bool(os.environ.get("KUBERNETES_SERVICE_HOST"))
        or bool(os.environ.get("container"))
    )


def _has_graphical_environment() -> bool:
    """Return whether the current platform appears able to open a browser."""
    if platform.system() in {"Windows", "Darwin"}:
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _is_interactive_terminal() -> bool:
    return _sys.stdout.isatty()


def _should_open_browser(requested: Optional[bool]) -> bool:
    """Resolve the CLI browser option against the current execution environment."""
    if requested is False or not dashboard_assets_available():
        return False
    if _env_truthy("CI") or _is_containerized():
        return False
    if any(
        os.environ.get(name) for name in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")
    ):
        return False
    if not _has_graphical_environment():
        return False
    if requested is True:
        return True
    return _is_interactive_terminal()


def _dashboard_url(host: str, port: int) -> str:
    """Build a browser-reachable Dashboard URL from a bind host and port."""
    reachable_host = (host or "").strip()
    if reachable_host in {"", "0.0.0.0"}:
        reachable_host = "127.0.0.1"
    elif reachable_host in {"::", "[::]"}:
        reachable_host = "::1"

    if ":" in reachable_host and not reachable_host.startswith("["):
        reachable_host = f"[{reachable_host}]"

    return f"http://{reachable_host}:{port}/dashboard/"


def _dashboard_is_ready(url: str, timeout: float = 1.0) -> bool:
    """Return whether the Dashboard URL currently responds successfully."""
    try:
        with _NO_PROXY_OPENER.open(url, timeout=timeout) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _wait_and_open_dashboard(
    url: str,
    *,
    timeout: float = 60.0,
    poll_interval: float = 0.5,
) -> None:
    """Wait for the Dashboard to become reachable, then open it once."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _dashboard_is_ready(url):
            try:
                if not webbrowser.open(url):
                    logger.warning(
                        "Could not open Dashboard in the default browser: %s", url
                    )
            except Exception:
                logger.warning(
                    "Could not open Dashboard in the default browser: %s",
                    url,
                    exc_info=True,
                )
            return
        time.sleep(poll_interval)

    logger.warning(
        "Dashboard did not become ready before browser-open timeout: %s", url
    )


def _start_dashboard_browser(host: str, port: int) -> None:
    """Start one background waiter that opens the Dashboard when it is ready."""
    url = _dashboard_url(host, port)
    if _dashboard_is_ready(url):
        logger.warning(
            "Dashboard is already reachable; skipping automatic browser open: %s", url
        )
        return

    threading.Thread(
        target=_wait_and_open_dashboard,
        args=(url,),
        name="powermem-dashboard-browser",
        daemon=True,
    ).start()


def _is_address_in_use(error: OSError) -> bool:
    return (
        error.errno in {errno.EADDRINUSE, _WINDOWS_ADDRESS_IN_USE}
        or getattr(error, "winerror", None) == _WINDOWS_ADDRESS_IN_USE
    )


def _assert_bind_available(host: str, port: int) -> None:
    """Fail early with a clear message when the configured bind address is busy."""
    if port == 0:
        return

    bind_host = (host or "").strip() or None
    try:
        addresses = socket.getaddrinfo(
            bind_host,
            port,
            type=socket.SOCK_STREAM,
            flags=socket.AI_PASSIVE,
        )
    except OSError:
        return

    for family, socktype, proto, _canonname, sockaddr in addresses:
        probe = socket.socket(family, socktype, proto)
        try:
            # Match uvicorn/asyncio TCP bind behavior so restart probes do not
            # reject ports that are only held by reusable TCP shutdown state.
            if not _sys.platform.startswith("win"):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(sockaddr)
        except OSError as exc:
            if _is_address_in_use(exc):
                display_host = host or "0.0.0.0"
                raise click.ClickException(
                    f"Port {port} is already in use on {display_host}. "
                    "Stop the existing process or choose another --port."
                ) from exc
        finally:
            probe.close()


def _is_embedded_storage() -> bool:
    """
    Check whether the configured storage backend is an embedded (single-process) database.

    Returns True for:
    - SQLite (always embedded, file-based)
    - OceanBase/seekdb in embedded mode (OCEANBASE_HOST is empty)
    """
    try:
        # Ensure `.env` is loaded before constructing settings classes that do not
        # read env files themselves (e.g. OceanBaseConfig uses env_file=None).
        from powermem.config_loader import _load_dotenv_if_available

        _load_dotenv_if_available()

        from powermem.config_loader import DatabaseSettings

        db_settings = DatabaseSettings()
        provider = db_settings.provider.lower()

        if provider == "sqlite":
            return True

        if provider == "oceanbase":
            from powermem.storage.config.oceanbase import OceanBaseConfig

            ob_config = OceanBaseConfig()
            return not (ob_config.host or "").strip()

    except Exception:
        pass

    return False


def _run_server_app(**kwargs) -> None:
    """Start uvicorn after verifying server extras are installed."""
    _require_server_deps()
    import uvicorn

    uvicorn.run(**kwargs)


def _setup_server_logging() -> None:
    """Configure server logging after verifying server extras are installed."""
    _require_server_deps()
    from ..middleware.logging import setup_logging

    setup_logging()


@click.command()
@click.option("--host", default=None, help="Host to bind to")
@click.option("--port", default=None, type=int, help="Port to bind to")
@click.option("--workers", default=None, type=int, help="Number of worker processes")
@click.option("--reload", is_flag=True, help="Enable auto-reload")
@click.option("--log-level", default=None, help="Log level")
@click.option(
    "--open-browser/--no-open-browser",
    default=None,
    help="Open the Dashboard after startup (auto-detected by default)",
)
def server(host, port, workers, reload, log_level, open_browser):
    """
    Start the PowerMem API server.

    Example:
        powermem-server --host 0.0.0.0 --port 8848 --reload
    """
    import sys

    # Override config with CLI options
    if host:
        config.host = host
    if port:
        config.port = port
    if workers:
        config.workers = workers
    if reload:
        config.reload = True
    if log_level:
        config.log_level = log_level

    # Embedded databases (SQLite / embedded seekdb) only support a single process.
    # Force workers=1 automatically so users don't have to set it manually.
    if not config.reload and config.workers != 1 and _is_embedded_storage():
        print(
            f"[server] Embedded storage detected (SQLite or seekdb without host). "
            f"Forcing workers=1 (was {config.workers}).",
            file=sys.stderr,
        )
        config.workers = 1

    # Setup logging BEFORE starting uvicorn to ensure all logs have timestamps
    _setup_server_logging()
    _assert_bind_available(config.host, config.port)

    if _should_open_browser(open_browser):
        _start_dashboard_browser(config.host, config.port)

    _run_server_app(
        app="server.main:app",
        host=config.host,
        port=config.port,
        reload=config.reload,
        workers=config.workers if not config.reload else 1,
        log_level=config.log_level.lower(),
    )


if __name__ == "__main__":
    server()
