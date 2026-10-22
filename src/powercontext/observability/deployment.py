"""Installed Docker Compose deployment with private, persistent configuration."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import subprocess
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import yaml
from platformdirs import user_data_path

TEMPLATE_VERSION = 1
STATE_VERSION = 1
BACKEND = "docker-compose"


class ObservabilityError(ValueError):
    """An actionable deployment or configuration failure without credential values."""


def state_directory() -> Path:
    configured = os.environ.get("POWERCONTEXT_OBSERVABILITY_HOME")
    return (
        Path(configured).expanduser().resolve()
        if configured
        else user_data_path("powercontext", appauthor=False) / "observability"
    )


def validate_backend(backend: str | None) -> None:
    if backend not in (None, BACKEND):
        raise ObservabilityError("Unsupported backend; available backend: docker-compose")  # noqa: TRY003


def load_state(directory: Path) -> dict[str, Any]:
    try:
        state = json.loads((directory / "state.json").read_text())
    except (OSError, ValueError) as error:
        raise ObservabilityError("No readable deployment state; run `powercontext observability up` first") from error  # noqa: TRY003
    if not isinstance(state, dict) or state.get("schema_version") != STATE_VERSION:
        raise ObservabilityError("Unsupported deployment state version; use the matching PowerContext version")  # noqa: TRY003
    if state.get("backend") != BACKEND:
        raise ObservabilityError("Unsupported saved backend; available backend: docker-compose")  # noqa: TRY003
    valid_fields = (
        isinstance(state.get("project"), str)
        and re.fullmatch(r"pc-observability-[0-9a-f]{12}", state["project"])
        and isinstance(state.get("server_url"), str)
        and isinstance(state.get("metrics_target"), str)
        and all(
            type(state.get(key)) is int and 1 <= state[key] <= 65535
            for key in ("grafana_port", "gateway_port", "phoenix_port")
        )
        and len({state["grafana_port"], state["gateway_port"], state["phoenix_port"]}) == 3
        and state.get("server_mode") in {"local", "docker"}
    )
    if not valid_fields:
        raise ObservabilityError("Deployment state is incomplete or invalid; restore its saved configuration")  # noqa: TRY003
    _server_url(state["server_url"])
    _metrics_target(state["server_url"], state["metrics_target"])
    if state.get("template_version") != TEMPLATE_VERSION:
        raise ObservabilityError("Deployment template version differs; use the matching PowerContext version")  # noqa: TRY003
    return state


def docker_command() -> str:
    executable = shutil.which("docker")
    desktop = Path("/Applications/Docker.app/Contents/Resources/bin/docker")
    if executable:
        return executable
    if desktop.is_file():
        return str(desktop)
    raise ObservabilityError("Docker is not installed or not on PATH; install Docker with Compose and retry")  # noqa: TRY003


def compose(directory: Path, state: dict[str, Any], *arguments: str, timeout: int = 120) -> str:
    command = [
        docker_command(),
        "compose",
        "--project-name",
        state["project"],
        "--file",
        str(directory / "compose.yaml"),
    ]
    try:
        result = subprocess.run(  # noqa: S603 - executable resolved locally; arguments never contain credentials.
            [*command, *arguments],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ObservabilityError(  # noqa: TRY003
            "Docker Compose did not complete; check Docker and use observability status before retrying"
        ) from error
    if result.returncode:
        raise ObservabilityError(  # noqa: TRY003
            "Docker Compose failed; check Docker, image access, ports and `observability status`. No data was deleted"
        )
    return result.stdout


@contextmanager
def deployment_lock(directory: Path):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    lock = directory / "operation.lock"
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise ObservabilityError(  # noqa: TRY003
            "Another operation holds operation.lock; if it crashed, confirm no operation is running before removing the lock"
        ) from error
    try:
        os.write(descriptor, str(os.getpid()).encode())
        yield
    finally:
        os.close(descriptor)
        lock.unlink()


def _private_write(path: Path, content: str, *, mode: int = 0o600) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(content)
    path.chmod(mode)


def _check_port(port: int) -> None:
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", port))
        except OSError as error:
            raise ObservabilityError(f"Port {port} is occupied; choose another port on first up") from error  # noqa: TRY003


def _server_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ObservabilityError("Server URL must be an HTTP(S) origin without embedded credentials")  # noqa: TRY003
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ObservabilityError("Server URL must not contain a path, query or fragment")  # noqa: TRY003
    try:
        _ = parsed.port
    except ValueError as error:
        raise ObservabilityError("Invalid Server URL port") from error  # noqa: TRY003
    return value.rstrip("/")


def _copy_resource(source, destination: Path) -> None:
    if source.is_dir():
        destination.mkdir(exist_ok=True)
        for child in source.iterdir():
            _copy_resource(child, destination / child.name)
    else:
        destination.write_bytes(source.read_bytes())


def _metrics_target(server_url: str, target: str | None) -> str:
    parsed = urlsplit(server_url)
    host = parsed.hostname or "localhost"
    if host in {"127.0.0.1", "localhost", "::1"}:
        host = "host.docker.internal"
    host = f"[{host}]" if ":" in host else host
    metrics_target = target or f"{host}:{parsed.port or (443 if parsed.scheme == 'https' else 80)}"
    try:
        selected = urlsplit("//" + metrics_target)
        valid = (
            selected.hostname
            and selected.port
            and not selected.username
            and not selected.password
            and not selected.path
            and not selected.query
            and not selected.fragment
            and not any(character.isspace() for character in metrics_target)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ObservabilityError("Metrics target must be host:port as reachable from Docker")  # noqa: TRY003
    return metrics_target


def initialize_statistics(directory: Path, *, server_url: str, token: str) -> None:
    """Give the internal read-only proxy a separate credential from the Server token."""
    local = directory / ".local"
    credential = local / "stats-proxy-token"
    if not credential.exists():
        _private_write(credential, secrets.token_urlsafe(32))
    proxy_token = credential.read_text().strip()
    _private_write(
        local / "stats.json",
        json.dumps({"server_url": _server_url(server_url), "server_token": token, "proxy_token": proxy_token}),
        mode=0o644,
    )
    _private_write(local / "stats.env", f"PC_STATS_PROXY_TOKEN={proxy_token}\n")


def initialize(
    directory: Path,
    *,
    server_url: str,
    token: str,
    grafana_port: int,
    gateway_port: int,
    target: str | None = None,
    server_mode: str = "local",
    server_uid: int = 10001,
    server_gid: int = 10001,
    background_target: str | None = None,
    phoenix_port: int = 6006,
) -> dict[str, Any]:
    server_url = _server_url(server_url)
    if server_mode not in {"local", "docker"}:
        raise ObservabilityError("Server mode must be local or docker")  # noqa: TRY003
    if len({grafana_port, gateway_port, phoenix_port}) != 3:
        raise ObservabilityError("Grafana, gateway and Phoenix ports must differ")  # noqa: TRY003
    for port in (grafana_port, gateway_port, phoenix_port):
        _check_port(port)
    parsed = urlsplit(server_url)
    if server_mode == "docker" and target is None:
        target = "powercontext:8000"
    metrics_target = _metrics_target(server_url, target)
    background_metrics_target = _metrics_target(server_url, background_target) if background_target else None
    local = directory / ".local"
    local.mkdir(mode=0o700, exist_ok=True)
    (local / "logs").mkdir(exist_ok=True)
    state = {
        "schema_version": STATE_VERSION,
        "template_version": TEMPLATE_VERSION,
        "backend": BACKEND,
        "project": "pc-observability-" + secrets.token_hex(6),
        "server_url": server_url,
        "grafana_port": grafana_port,
        "gateway_port": gateway_port,
        "metrics_target": metrics_target,
        "server_mode": server_mode,
        "background_target": background_target,
        "phoenix_port": phoenix_port,
    }
    # Resources have one owner; editable deployment copies remain stable across repeated up/package upgrades.
    source = resources.files("powercontext.observability").joinpath("resources")

    _copy_resource(source, directory)
    _private_write(local / "observe-token", token, mode=0o644)
    initialize_statistics(directory, server_url=f"{parsed.scheme}://{metrics_target}", token=token)
    password_path = local / "gateway-password"
    if not password_path.exists():
        _private_write(password_path, secrets.token_urlsafe(32))
    password = password_path.read_text().strip()
    grafana_password = local / "grafana-password"
    if not grafana_password.exists():
        _private_write(grafana_password, secrets.token_urlsafe(32), mode=0o644)
    # nginx supports the RFC 2307 SHA password format. The credential has 256 bits of random entropy.
    digest = base64.b64encode(hashlib.sha1(password.encode(), usedforsecurity=False).digest()).decode()
    _private_write(local / "htpasswd", "collector:{SHA}" + digest + "\n", mode=0o644)
    targets = [{"targets": [metrics_target], "labels": {"role": "api" if background_target else "all"}}]
    if background_metrics_target:
        targets.append({"targets": [background_metrics_target], "labels": {"role": "background"}})
    _private_write(local / "targets.json", json.dumps(targets), mode=0o644)
    prometheus_file = directory / "prometheus" / "prometheus.yaml"
    prometheus = yaml.safe_load(prometheus_file.read_text())
    prometheus["scrape_configs"][0]["scheme"] = parsed.scheme
    if not token:
        prometheus["scrape_configs"][0].pop("authorization", None)
    prometheus_file.write_text(yaml.safe_dump(prometheus, sort_keys=False))
    compose_file = directory / "compose.yaml"
    manifest = yaml.safe_load(compose_file.read_text())
    manifest.pop("name", None)
    manifest["services"]["grafana"]["ports"] = [f"127.0.0.1:{grafana_port}:3000"]
    manifest["services"]["gateway"]["ports"] = [f"127.0.0.1:{gateway_port}:8080"]
    manifest["services"]["phoenix"]["ports"] = [f"127.0.0.1:{phoenix_port}:6006"]
    if server_mode == "docker":
        _container_configuration(directory, state, manifest, uid=server_uid, gid=server_gid)
    compose_file.write_text(yaml.safe_dump(manifest, sort_keys=False))
    authorization = base64.b64encode(f"collector:{password}".encode()).decode()
    profile = {
        "POWERCONTEXT_SERVER_TRACING_ENABLED": "true",
        "POWERCONTEXT_SERVER_LOGGING_DIRECTORY": str(local / "logs"),
        "POWERCONTEXT_SERVER_LOGGING_OTLP_ENABLED": "true",
        "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{gateway_port}",
        "OTEL_EXPORTER_OTLP_HEADERS": f"Authorization=Basic%20{authorization}",
        "OTEL_EXPORTER_OTLP_TIMEOUT": "2",
        "OTEL_BSP_MAX_QUEUE_SIZE": "512",
        "OTEL_BSP_MAX_EXPORT_BATCH_SIZE": "128",
        "OTEL_BSP_SCHEDULE_DELAY": "1000",
        "OTEL_BLRP_MAX_QUEUE_SIZE": "512",
        "OTEL_BLRP_MAX_EXPORT_BATCH_SIZE": "128",
        "OTEL_BLRP_SCHEDULE_DELAY": "1000",
        "OTEL_BLRP_EXPORT_TIMEOUT": "2000",
        "OTEL_TRACES_SAMPLER": "parentbased_traceidratio",
        "OTEL_TRACES_SAMPLER_ARG": "0.1",
        "OTEL_RESOURCE_ATTRIBUTES": (
            f"powercontext.service.api_target={metrics_target}"
            + (
                f",powercontext.service.background_target={background_metrics_target}"
                if background_metrics_target
                else ""
            )
        ),
    }
    _private_write(directory / "server.env", "".join(f"{key}={shlex.quote(value)}\n" for key, value in profile.items()))
    profile["POWERCONTEXT_SERVER_LOGGING_DIRECTORY"] = "/var/log/powercontext"
    profile["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://gateway:8080"
    _private_write(
        directory / "container.env", "".join(f"{key}={shlex.quote(value)}\n" for key, value in profile.items())
    )
    _private_write(directory / "state.json", json.dumps(state, indent=2) + "\n")
    return state


def backend_client(directory: Path, state: dict[str, Any]) -> httpx.Client:
    return httpx.Client(
        base_url=f"http://127.0.0.1:{state['gateway_port']}",
        auth=("collector", (directory / ".local" / "gateway-password").read_text().strip()),
        timeout=5,
        trust_env=False,
    )


def _container_configuration(
    directory: Path, state: dict[str, Any], manifest: dict[str, Any], *, uid: int, gid: int
) -> None:
    """Generate an override for an existing official-image service, retaining its data and command."""
    volume = state["project"] + "_logs"
    manifest["volumes"]["server-logs"] = {"name": volume}
    collector = manifest["services"]["collector"]
    collector["volumes"] = [
        item if item != "./.local/logs:/logs:ro" else "server-logs:/logs:ro" for item in collector["volumes"]
    ]
    manifest["services"]["log-permissions"] = {
        "image": manifest["services"]["gateway"]["image"],
        "user": "0:0",
        "entrypoint": ["/bin/sh", "-c"],
        "command": [f"chown {uid}:{gid} /logs && chmod 750 /logs"],
        "volumes": ["server-logs:/logs"],
        "restart": "no",
        "mem_limit": "32m",
        "cpus": 0.25,
        "logging": {"driver": "json-file", "options": {"max-size": "1m", "max-file": "1"}},
    }
    collector["depends_on"] = {
        "tempo": {"condition": "service_started"},
        "loki": {"condition": "service_started"},
        "log-permissions": {"condition": "service_completed_successfully"},
    }
    override = {
        "services": {
            "powercontext": {
                "env_file": [str(directory / "container.env")],
                "networks": ["default", "observability"],
                "volumes": ["observability-logs:/var/log/powercontext"],
            }
        },
        "networks": {"observability": {"external": True, "name": state["project"] + "_default"}},
        "volumes": {"observability-logs": {"external": True, "name": volume}},
    }
    (directory / "server.compose.yaml").write_text(yaml.safe_dump(override, sort_keys=False))


def wait_for_backends(directory: Path, state: dict[str, Any]) -> None:
    """Require queryable backends, rather than equating a running container with readiness."""
    import time

    deadline = time.monotonic() + 60
    with backend_client(directory, state) as client:
        while time.monotonic() < deadline:
            try:
                responses = [client.get(path) for path in ("/tempo/ready", "/loki/ready", "/prometheus/-/ready")]
                grafana = httpx.get(f"http://127.0.0.1:{state['grafana_port']}/api/health", timeout=3, trust_env=False)
                if all(response.status_code == 200 for response in responses) and grafana.status_code == 200:
                    return
            except httpx.TransportError:
                pass
            time.sleep(1)
    raise ObservabilityError(  # noqa: TRY003
        "Monitoring backends did not become queryable within 60 seconds; inspect observability status and component logs"
    )
