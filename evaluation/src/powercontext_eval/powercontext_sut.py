"""Disposable Docker lifecycle for balanced PowerContext treatments."""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol
from urllib.parse import quote, quote_plus, urlsplit

from powercontext_eval import docker_pressure
from powercontext_eval.artifacts import ArtifactStore
from powercontext_eval.codex import (
    DEFAULT_CODEX_MODEL,
    DEFAULT_REASONING_EFFORT,
    EXPECTED_CODEX_VERSION,
    CodexInfrastructureError,
    CodexInvocation,
    CodexOutcome,
    CodexRunner,
    is_safe_codex_model,
)
from powercontext_eval.errors import CommandError, CommandFailed, CommandTimedOut, PowerContextEvalError
from powercontext_eval.models import Arm
from powercontext_eval.process import CommandResult, ProcessRunner
from powercontext_eval.tokensflow import (
    DrainDeadline,
    TokensFlowDaemonHandle,
    TokensFlowEvidence,
    TokensFlowFinalizationDescriptor,
    TokensFlowFinalizationRegistrar,
    TokensFlowInfrastructureError,
    UnsafeTokensFlowConfiguration,
    matched_tokensflow_evidence,
    parse_tokensflow_version,
    snapshot_tokensflow_home,
    tokensflow_queue_caught_up,
    tokensflow_queue_negative_detected,
    tokensflow_runtime_environment,
    tokensflow_secret_variants,
)

PLUGIN_ID = "powercontext@powercontext"
_SAFE_RUN_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
_SAFE_DOCKER_NETWORK = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_SHA = re.compile(r"[0-9a-f]{40}")
_INVALID_DOCKER_COPY_SYMLINK = re.compile(r'invalid symlink "[^"\r\n]+" -> "[^"\r\n]+"')
_DOCKER_NETWORK_CONTROL_LOCK = threading.Lock()
_DOCKER_NETWORK_CREATE_ATTEMPTS = 3
_DOCKER_NETWORK_CREATE_RETRY_SECONDS = 0.25
# Use RFC1918 space that behaves like the host's existing Docker bridges.  The
# RFC 2544 benchmark block can be created by Docker but is not permitted to
# reach the host-bound proxy relay on the evaluation fleet.
DEFAULT_DOCKER_NETWORK_POOL = "172.30.0.0/15"
_DOCKER_NETWORK_PREFIX_LENGTH = 28
_DOCKER_NETWORK_SUBNET_ATTEMPTS = 64
_DOCKER_NETWORK_SUBNET_COLLISION_MARKERS = (
    "pool overlaps with other one on this address space",
    "requested subnet overlaps",
)
_CONTAINER_CODEX = "/tools/codex-dir/codex"
_CONTAINER_TOKENSFLOW = "/tools/tokensflow-dir/tokensflow"
_CONTAINER_TOKENSFLOW_WRAPPER_DIR = "/tools/tokensflow-wrapper"
_CONTAINER_UV = "/tools/uv-dir/uv"
_CONTAINER_RECORDER = "/evaluation/record_codex_jsonl.py"
_CONTAINER_UV_PYTHON_INSTALL_DIR = "/runtime/uv-python"
_DEFAULT_RECORDER_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "record_codex_jsonl.py"
_TIMED_OUT_CONTAINER_REMOVAL_SETTLE_SECONDS = 90.0
_TIMED_OUT_CONTAINER_REMOVAL_POLL_SECONDS = 0.25
_READINESS_BUDGET_SECONDS = 120.0
_READINESS_ATTEMPT_TIMEOUT_SECONDS = 10.0
_READINESS_RETRY_SECONDS = 0.5
_PLUGIN_LIST_BUDGET_SECONDS = 120.0
_PLUGIN_LIST_ATTEMPT_TIMEOUT_SECONDS = 60.0
_PLUGIN_LIST_RETRY_SECONDS = 0.5
_SERVER_READINESS_PROBE_SCRIPT = """
import json
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

request = Request("http://127.0.0.1:8000/health/ready", headers={"Accept": "application/json"})
code = 0
payload = None
try:
    with urlopen(request, timeout=3) as response:
        payload = json.load(response)
except HTTPError as error:
    code = 10 if error.code == 503 else 12
except json.JSONDecodeError:
    code = 11
except (OSError, ValueError):
    code = 10
if code == 0 and (not isinstance(payload, dict) or payload.get("status") != "ready"):
    code = 11
sys.exit(code)
""".strip()
LOOPBACK_NO_PROXY = "127.0.0.1,localhost,::1"
_TOKENSFLOW_RETRY_ATTEMPTS = 10
_PLUGIN_RELATIVE = Path("integrations/codex/plugins/powercontext")
_TOKENSFLOW_WRAPPER = b"""#!/bin/sh
real=${POWERCONTEXT_EVAL_TOKENSFLOW_REAL_BINARY-}
case "$real" in
  /*/tokensflow) ;;
  *) exit 126 ;;
esac
test -f "$real" && test -x "$real" || exit 126
unset ALL_PROXY HTTPS_PROXY HTTP_PROXY NO_PROXY all_proxy https_proxy http_proxy no_proxy
exec "$real" "$@"
"""


class InvalidTreatment(PowerContextEvalError):
    """Observed evidence does not prove the requested treatment."""


class PluginInspectionFailureReason(StrEnum):
    """Fixed, non-sensitive reasons why isolated plugin inspection did not converge."""

    TIMED_OUT = "timed_out"
    INVALID_PLUGIN_SET = "invalid_plugin_set"


_PLUGIN_INSPECTION_FAILURE_SUMMARIES = MappingProxyType(
    {
        PluginInspectionFailureReason.TIMED_OUT: "Isolated Codex plugin inspection timed out.",
        PluginInspectionFailureReason.INVALID_PLUGIN_SET: "Isolated Codex home did not converge to one plugin.",
    }
)


class PluginInspectionFailure(InvalidTreatment):
    """A retryable failure from inspecting the isolated Codex plugin installation."""

    def __init__(self, reason: PluginInspectionFailureReason) -> None:
        if not isinstance(reason, PluginInspectionFailureReason):
            raise TypeError("Plugin inspection failure reason must be classified")
        self.reason = reason
        super().__init__(_PLUGIN_INSPECTION_FAILURE_SUMMARIES[reason])

    @property
    def safe_summary(self) -> str:
        """Return the fixed user-visible failure summary."""

        return _PLUGIN_INSPECTION_FAILURE_SUMMARIES[self.reason]


class ReadinessFailureReason(StrEnum):
    """Fixed, non-sensitive reasons why the isolated Server readiness gate failed."""

    COMMAND_TIMED_OUT = "command_timed_out"
    SERVER_NOT_READY = "server_not_ready"
    MALFORMED_RESPONSE = "malformed_response"
    PROBE_FAILED = "probe_failed"


_READINESS_FAILURE_SUMMARIES = MappingProxyType(
    {
        ReadinessFailureReason.COMMAND_TIMED_OUT: "PowerContext readiness probe timed out.",
        ReadinessFailureReason.SERVER_NOT_READY: "PowerContext Server remained not ready before the deadline.",
        ReadinessFailureReason.MALFORMED_RESPONSE: "PowerContext Server returned malformed readiness evidence.",
        ReadinessFailureReason.PROBE_FAILED: "PowerContext readiness probe failed.",
    }
)


class ReadinessFailure(InvalidTreatment):
    """A safe, classified failure from the isolated Server readiness gate."""

    def __init__(self, reason: ReadinessFailureReason) -> None:
        if not isinstance(reason, ReadinessFailureReason):
            raise TypeError("Readiness failure reason must be classified")
        self.reason = reason
        super().__init__(_READINESS_FAILURE_SUMMARIES[reason])

    @property
    def safe_summary(self) -> str:
        """Return the fixed user-visible failure summary."""

        return _READINESS_FAILURE_SUMMARIES[self.reason]


class UnsafeSutConfiguration(PowerContextEvalError):
    """A SUT value could escape its owned resource boundary."""


@dataclass(frozen=True)
class SourceProvenance:
    checkout_sha: str
    plugin_version: str
    plugin_manifest_sha256: str

    def as_dict(self) -> dict[str, str]:
        return {
            "checkout_sha": self.checkout_sha,
            "plugin_version": self.plugin_version,
            "plugin_manifest_sha256": self.plugin_manifest_sha256,
        }


def loopback_proxy_environment(relay_url: str, extra_no_proxy_hosts: tuple[str, ...] = ()) -> dict[str, str]:
    """Return the exact case-balanced proxy environment for every container phase."""

    no_proxy = ",".join((LOOPBACK_NO_PROXY, *extra_no_proxy_hosts))
    return {
        "HTTPS_PROXY": relay_url,
        "HTTP_PROXY": relay_url,
        "https_proxy": relay_url,
        "http_proxy": relay_url,
        "NO_PROXY": no_proxy,
        "no_proxy": no_proxy,
    }


def direct_egress_environment() -> dict[str, str]:
    """Override inherited proxy variables for a runtime attached to its configured egress network."""

    return {
        "ALL_PROXY": "",
        "HTTPS_PROXY": "",
        "HTTP_PROXY": "",
        "all_proxy": "",
        "https_proxy": "",
        "http_proxy": "",
        "NO_PROXY": "",
        "no_proxy": "",
    }


def runtime_proxy_environment(
    relay_url: str | None,
    extra_no_proxy_hosts: tuple[str, ...] = (),
) -> dict[str, str]:
    """Use the configured relay or explicitly clear inherited proxy settings."""

    if relay_url is None:
        return direct_egress_environment()
    return loopback_proxy_environment(relay_url, extra_no_proxy_hosts)


def default_docker_bridge_gateway(process: ProcessRunner, cwd: Path) -> str:
    """Inspect and validate Docker's existing default bridge gateway."""

    result = process.run(
        ("docker", "network", "inspect", "bridge", "--format={{(index .IPAM.Config 0).Gateway}}"),
        cwd=cwd,
        timeout=30,
    )
    return _validated_gateway(result.stdout.strip())


def auth_secret_variants(auth_json: Path) -> tuple[str, ...]:
    """Extract nested scalar credentials and conservative encoded derivatives without logging them."""

    try:
        descriptor = os.open(auth_json, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise UnsafeSutConfiguration("Auth source must be a regular file")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                value = json.load(stream)
        finally:
            os.close(descriptor)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise UnsafeSutConfiguration("Auth JSON is not a safe JSON file") from error

    raw_values: set[str] = set()

    def visit(item: object) -> None:
        if isinstance(item, dict):
            for nested in item.values():
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)
        elif item is not None and isinstance(item, (str, int, float, bool)):
            text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False, allow_nan=False)
            if text:
                raw_values.add(text)

    visit(value)
    variants: set[str] = set()
    for raw in raw_values:
        encoded = raw.encode("utf-8")
        variants.update(
            {
                raw,
                quote(raw, safe=""),
                quote_plus(raw, safe=""),
                base64.b64encode(encoded).decode("ascii"),
                base64.urlsafe_b64encode(encoded).decode("ascii"),
                encoded.hex(),
            }
        )
    return tuple(sorted(variants, key=lambda item: (-len(item), item)))


def _retain_private_trace(
    source: Path,
    store: ArtifactStore,
    destination: str,
    *,
    required: bool,
) -> Path | None:
    descriptor = -1
    try:
        descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    except FileNotFoundError:
        if required:
            raise CodexInfrastructureError("Codex timestamp trace is missing") from None
        return None
    except OSError as error:
        raise CodexInfrastructureError("Context trace cannot be opened safely") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise CodexInfrastructureError("Context trace is not a regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            return store.write_stream(destination, stream)
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class TreatmentEvidence:
    """Strict post-run treatment evidence."""

    plugin_installed: bool
    plugin_id: str
    plugin_version: str
    plugin_checkout_sha: str
    server_ready: bool
    prompt_sources: int
    mcp_requests: int
    scope_id: str

    def __post_init__(self) -> None:
        if type(self.plugin_installed) is not bool or type(self.server_ready) is not bool:
            raise TypeError("Treatment booleans must be exact bool values")
        if not self.plugin_id or not self.plugin_version or _SHA.fullmatch(self.plugin_checkout_sha) is None:
            raise ValueError("Treatment plugin provenance is invalid")
        if (
            isinstance(self.prompt_sources, bool)
            or not isinstance(self.prompt_sources, int)
            or self.prompt_sources < 0
            or isinstance(self.mcp_requests, bool)
            or not isinstance(self.mcp_requests, int)
            or self.mcp_requests < 0
        ):
            raise ValueError("Treatment counters must be non-negative integers")
        if not self.scope_id:
            raise ValueError("Treatment scope must not be empty")

    def as_dict(self) -> dict[str, object]:
        return {
            "plugin_installed": self.plugin_installed,
            "plugin_id": self.plugin_id,
            "plugin_version": self.plugin_version,
            "plugin_checkout_sha": self.plugin_checkout_sha,
            "server_ready": self.server_ready,
            "prompt_sources": self.prompt_sources,
            "mcp_requests": self.mcp_requests,
            "scope_id": self.scope_id,
        }

    @classmethod
    def from_json(cls, raw: str) -> TreatmentEvidence:
        try:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise TypeError
            return cls(**value)
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            raise InvalidTreatment("Treatment evidence is malformed") from error


def validate_treatment(
    arm: Arm,
    run_id: str,
    evidence: TreatmentEvidence,
    *,
    expected_plugin_version: str,
    expected_checkout_sha: str,
) -> None:
    """Fail closed unless the exact expected treatment is proven."""

    expected_scope = f"eval:{run_id}:{arm.value}"
    common = (
        evidence.plugin_installed
        and evidence.plugin_id == PLUGIN_ID
        and evidence.plugin_version == expected_plugin_version
        and evidence.plugin_checkout_sha == expected_checkout_sha
        and evidence.server_ready
        and evidence.scope_id == expected_scope
    )
    activity = (
        evidence.prompt_sources >= 1 if arm is Arm.ON else evidence.prompt_sources == 0 and evidence.mcp_requests == 0
    )
    if not common or not activity:
        raise InvalidTreatment("Treatment evidence does not match the requested arm")


@dataclass(frozen=True)
class ProxyRelayConfig:
    """Credential-free loopback proxy upstream."""

    url: str

    def __post_init__(self) -> None:
        parsed = urlsplit(self.url)
        try:
            port = parsed.port
        except ValueError as error:
            raise UnsafeSutConfiguration("Proxy upstream is invalid") from error
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"127.0.0.1", "::1"}
            or port is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise UnsafeSutConfiguration("Proxy upstream must be a credential-free loopback URL")


@dataclass(frozen=True)
class ContainerLimits:
    cpus: str = "2"
    memory: str = "4g"
    pids: int = 256

    def __post_init__(self) -> None:
        if re.fullmatch(r"[1-9][0-9]*(?:\.[0-9]+)?", self.cpus) is None:
            raise UnsafeSutConfiguration("CPU limit is unsafe")
        if re.fullmatch(r"[1-9][0-9]*[kmg]", self.memory.lower()) is None or not 1 <= self.pids <= 4096:
            raise UnsafeSutConfiguration("Container limits are unsafe")


@dataclass(frozen=True)
class SutConfig:
    """Pinned inputs shared by both treatment arms."""

    run_id: str
    task_image: str
    codex_binary: Path
    uv_binary: Path
    source_checkout: Path
    plugin_checkout_sha: str
    tokensflow_enabled: bool = False
    tokensflow_binary: Path | None = None
    tokensflow_egress_network: str | None = None
    proxy: ProxyRelayConfig | None = None
    docker_network_pool: str = DEFAULT_DOCKER_NETWORK_POOL
    extra_no_proxy_hosts: tuple[str, ...] = ()
    model: str = DEFAULT_CODEX_MODEL
    reasoning_effort: str = DEFAULT_REASONING_EFFORT
    recorder_script: Path = _DEFAULT_RECORDER_SCRIPT
    limits: ContainerLimits = ContainerLimits()
    plugin_version: str = "0.1.0"
    codex_timeout: float = 3600
    finalization_registrar: TokensFlowFinalizationRegistrar | None = None
    container_env: Mapping[str, str] = MappingProxyType({})

    def __post_init__(self) -> None:
        if _SAFE_RUN_ID.fullmatch(self.run_id) is None:
            raise UnsafeSutConfiguration("Run id is unsafe")
        if (
            not self.task_image
            or self.task_image.startswith("-")
            or any(char in self.task_image for char in "\0 \t\r\n")
        ):
            raise UnsafeSutConfiguration("Task image is unsafe")
        if _SHA.fullmatch(self.plugin_checkout_sha) is None:
            raise UnsafeSutConfiguration("Plugin checkout SHA is unsafe")
        if not is_safe_codex_model(self.model):
            raise UnsafeSutConfiguration("Codex model is unsafe")
        if self.reasoning_effort != DEFAULT_REASONING_EFFORT:
            raise UnsafeSutConfiguration("Unsupported Codex reasoning effort")
        for path in (self.codex_binary, self.uv_binary, self.source_checkout, self.recorder_script):
            if not path.is_absolute() or "\0" in os.fspath(path):
                raise UnsafeSutConfiguration("SUT paths must be absolute")
        if self.tokensflow_enabled:
            if (
                self.tokensflow_binary is None
                or not self.tokensflow_binary.is_absolute()
                or "\0" in os.fspath(self.tokensflow_binary)
            ):
                raise TokensFlowInfrastructureError("TokensFlow binary path is unsafe")
            if (
                self.tokensflow_egress_network is None
                or _SAFE_DOCKER_NETWORK.fullmatch(self.tokensflow_egress_network) is None
            ):
                raise TokensFlowInfrastructureError("TokensFlow egress network is unsafe")
        _validated_docker_network_pool(self.docker_network_pool)
        _validated_no_proxy_hosts(self.extra_no_proxy_hosts)
        try:
            recorder_metadata = self.recorder_script.stat(follow_symlinks=False)
        except OSError as error:
            raise UnsafeSutConfiguration("Codex recorder script is missing") from error
        if not stat.S_ISREG(recorder_metadata.st_mode):
            raise UnsafeSutConfiguration("Codex recorder script must be a regular file")
        if self.codex_timeout <= 0:
            raise UnsafeSutConfiguration("Codex timeout must be positive")


@dataclass(frozen=True)
class ArmPaths:
    """Ephemeral inputs and retained result root for one arm."""

    source: Path
    auth_source: Path
    workspace: Path
    runtime: Path
    codex_home: Path
    pc_home: Path
    tokensflow_home: Path
    result_root: Path
    codex_config_source: Path | None = None

    def __post_init__(self) -> None:
        paths = (
            self.source,
            self.auth_source,
            self.workspace,
            self.runtime,
            self.codex_home,
            self.pc_home,
            self.result_root,
        )
        if any(not path.is_absolute() for path in paths):
            raise UnsafeSutConfiguration("Arm paths must be absolute")
        if self.codex_config_source is not None and not self.codex_config_source.is_absolute():
            raise UnsafeSutConfiguration("Codex config source must be absolute")
        if self.codex_home.is_relative_to(self.result_root) or self.pc_home.is_relative_to(self.result_root):
            raise UnsafeSutConfiguration("Private homes must remain outside retained results")
        if not self.codex_home.is_relative_to(self.runtime) or not self.pc_home.is_relative_to(self.runtime):
            raise UnsafeSutConfiguration("Private homes must remain within the ephemeral runtime")
        if not self.tokensflow_home.is_absolute():
            raise TokensFlowInfrastructureError("TokensFlow home path is unsafe")
        if self.tokensflow_home.is_relative_to(self.result_root):
            raise TokensFlowInfrastructureError("TokensFlow home path is unsafe")
        if not self.tokensflow_home.is_relative_to(self.runtime):
            raise TokensFlowInfrastructureError("TokensFlow home path is unsafe")

    def prepare(self) -> None:
        try:
            self.workspace.mkdir(parents=True, exist_ok=False, mode=0o700)
        except FileExistsError as error:
            raise UnsafeSutConfiguration("Arm workspace and runtime must be fresh") from error
        if self.workspace.is_symlink():
            raise UnsafeSutConfiguration("Arm workspace and runtime must be fresh directories")
        try:
            self.runtime.mkdir(parents=True, exist_ok=False, mode=0o700)
        except FileExistsError as error:
            if self.runtime.is_symlink() or not self.runtime.is_dir():
                raise UnsafeSutConfiguration("Arm workspace and runtime must be fresh directories") from error
            if self.tokensflow_home.parent != self.runtime or set(self.runtime.iterdir()) != {self.tokensflow_home}:
                raise UnsafeSutConfiguration("Arm runtime must be fresh except for the private TokensFlow home")
        if self.runtime.is_symlink():
            raise UnsafeSutConfiguration("Arm workspace and runtime must be fresh directories")
        for path in (self.codex_home, self.pc_home):
            path.mkdir(mode=0o700)

    def copy_auth(self) -> Path:
        """Copy only auth.json through no-follow descriptors at mode 0600."""

        self.codex_home.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination = self.codex_home / "auth.json"
        source_fd = os.open(self.auth_source, os.O_RDONLY | os.O_NOFOLLOW)
        destination_fd = -1
        try:
            metadata = os.fstat(source_fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise UnsafeSutConfiguration("Auth source must be a regular file")
            destination_fd = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            while chunk := os.read(source_fd, 64 * 1024):
                os.write(destination_fd, chunk)
            os.fchmod(destination_fd, 0o600)
            os.fsync(destination_fd)
        except FileExistsError as error:
            raise UnsafeSutConfiguration("Ephemeral auth destination already exists") from error
        finally:
            os.close(source_fd)
            if destination_fd >= 0:
                os.close(destination_fd)
        return destination

    def copy_codex_config(self) -> Path | None:
        """Copy an optional provider-only config through no-follow descriptors at mode 0600."""

        if self.codex_config_source is None:
            return None
        self.codex_home.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination = self.codex_home / "config.toml"
        source_fd = os.open(self.codex_config_source, os.O_RDONLY | os.O_NOFOLLOW)
        destination_fd = -1
        try:
            metadata = os.fstat(source_fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise UnsafeSutConfiguration("Codex config source must be a regular file")
            destination_fd = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            while chunk := os.read(source_fd, 64 * 1024):
                os.write(destination_fd, chunk)
            os.fchmod(destination_fd, 0o600)
            os.fsync(destination_fd)
        except FileExistsError as error:
            raise UnsafeSutConfiguration("Ephemeral Codex config destination already exists") from error
        finally:
            os.close(source_fd)
            if destination_fd >= 0:
                os.close(destination_fd)
        return destination


class ProxyRelay(Protocol):
    def start(self, gateway: str, upstream: ProxyRelayConfig) -> str: ...

    def stop(self) -> None: ...


class SocatProxyRelay:
    """One exact host process bound only to an internal bridge gateway."""

    def __init__(self, *, executable: str = "socat", readiness_timeout: float = 5.0) -> None:
        self._executable = executable
        self._timeout = readiness_timeout
        self._process: subprocess.Popen[bytes] | None = None

    def start(self, gateway: str, upstream: ProxyRelayConfig) -> str:
        address = _validated_gateway(gateway)
        parsed = urlsplit(upstream.url)
        assert parsed.hostname is not None and parsed.port is not None
        port = _reserve_port(address)
        argv = (
            self._executable,
            f"TCP-LISTEN:{port},bind={address},fork,reuseaddr",
            f"TCP:{parsed.hostname}:{parsed.port}",
        )
        try:
            self._process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                start_new_session=True,
                shell=False,
                env={"PATH": os.environ.get("PATH", "")},
            )
            self._wait_ready(address, port)
        except BaseException:
            self.stop()
            raise
        return f"{parsed.scheme}://{address}:{port}"

    def _wait_ready(self, gateway: str, port: int) -> None:
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            process = self._process
            if process is None or process.poll() is not None:
                raise UnsafeSutConfiguration("Proxy relay exited before readiness")
            try:
                with socket.create_connection((gateway, port), timeout=0.1):
                    return
            except OSError:
                time.sleep(0.02)
        raise UnsafeSutConfiguration("Proxy relay readiness timed out")

    def stop(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        if process.stderr is not None:
            process.stderr.close()


@dataclass(frozen=True)
class SutOutcome:
    codex: CodexOutcome
    evidence: TreatmentEvidence
    tokensflow: TokensFlowEvidence | None = None
    tokensflow_daemon: TokensFlowDaemonHandle | None = None


class _DockerExecRunner(ProcessRunner):
    def __init__(self, runner: Any, container: str) -> None:
        self._delegate = runner
        self._container = container

    def run(self, argv: Any, **kwargs: Any) -> CommandResult:
        environment = kwargs.pop("env", None)
        docker_environment: tuple[str, ...] = ()
        if environment:
            docker_environment = tuple(part for item in environment.items() for part in ("-e", f"{item[0]}={item[1]}"))
        # Docker's attached exec streams become unreliable under a full 20-task
        # wave. Keep worker capacity at 20 while sharing one process-wide daemon
        # budget with official harnesses and workspace extraction.
        with docker_pressure.heavy_operation():
            return self._delegate.run(
                ("docker", "exec", "-i", *docker_environment, self._container, *tuple(argv)),
                **kwargs,
            )


class _DockerPressureRunner(ProcessRunner):
    """Route every Docker SUT command through the shared daemon budget."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate

    def run(self, argv: Any, **kwargs: Any) -> CommandResult:
        with docker_pressure.heavy_operation():
            return self._delegate.run(argv, **kwargs)


class DockerSut:
    """Execute one arm while owning only run-prefixed Docker resources."""

    def __init__(
        self,
        docker: Any,
        *,
        relay_factory: Callable[[], ProxyRelay] = SocatProxyRelay,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._docker = _DockerPressureRunner(docker)
        self._relay_factory = relay_factory
        self._clock = clock
        self._sleeper = sleeper

    def network_gateway(self, network: str, cwd: Path) -> str:
        if (
            not network.startswith("powercontext-eval-")
            or _SAFE_RUN_ID.fullmatch(network.removeprefix("powercontext-eval-")) is None
        ):
            raise UnsafeSutConfiguration("Network name is unsafe")
        result = self._docker.run(("docker", "network", "inspect", network), cwd=cwd, timeout=30)
        try:
            value = json.loads(result.stdout)
            gateway = value[0]["IPAM"]["Config"][0]["Gateway"]
            if not isinstance(gateway, str):
                raise TypeError
        except (json.JSONDecodeError, IndexError, KeyError, TypeError) as error:
            raise UnsafeSutConfiguration("Docker network inspect did not provide one gateway") from error
        return _validated_gateway(gateway)

    def _verify_source(self, config: SutConfig) -> SourceProvenance:
        """Verify immutable checkout and plugin manifest before creating resources."""

        result = self._docker.run(
            ("git", "rev-parse", "--verify", "HEAD^{commit}"),
            cwd=config.source_checkout,
            timeout=30,
        )
        actual_sha = result.stdout.strip()
        if _SHA.fullmatch(actual_sha) is None or actual_sha != config.plugin_checkout_sha:
            raise InvalidTreatment("PowerContext source HEAD does not match the configured commit")
        status = self._docker.run(
            (
                "git",
                "status",
                "--porcelain",
                "--untracked-files=all",
            ),
            cwd=config.source_checkout,
            timeout=30,
        )
        if status.stdout:
            raise InvalidTreatment("PowerContext source checkout must be clean")
        manifest = config.source_checkout / _PLUGIN_RELATIVE / ".codex-plugin/plugin.json"
        try:
            descriptor = os.open(manifest, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
            try:
                with os.fdopen(descriptor, "rb", closefd=False) as stream:
                    manifest_bytes = stream.read()
                    value = json.loads(manifest_bytes)
            finally:
                os.close(descriptor)
            version = value["version"]
            if not isinstance(version, str) or not version or value.get("name") != "powercontext":
                raise TypeError
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
            raise InvalidTreatment("PowerContext plugin manifest is invalid") from error
        if version != config.plugin_version:
            raise InvalidTreatment("PowerContext plugin manifest version does not match configuration")
        lockfile = config.source_checkout / _PLUGIN_RELATIVE / "uv.lock"
        try:
            metadata = lockfile.stat(follow_symlinks=False)
        except OSError as error:
            raise InvalidTreatment("PowerContext plugin lockfile is missing") from error
        if not stat.S_ISREG(metadata.st_mode):
            raise InvalidTreatment("PowerContext plugin lockfile is invalid")
        return SourceProvenance(actual_sha, version, hashlib.sha256(manifest_bytes).hexdigest())

    def run_arm(
        self,
        config: SutConfig,
        arm: Arm,
        paths: ArmPaths,
        prompt: bytes,
        store: ArtifactStore,
    ) -> SutOutcome:
        source_provenance = self._verify_source(config)
        if config.tokensflow_enabled:
            self._validate_tokensflow_source(config, paths)
        with self._run_network(config, config.source_checkout) as (network, relay_url):
            return self._execute_arm(config, arm, paths, prompt, store, network, relay_url, source_provenance)

    def run_pair(
        self,
        config: SutConfig,
        *,
        paths: Mapping[Arm, ArmPaths],
        prompts: Mapping[Arm, bytes],
        stores: Mapping[Arm, ArtifactStore],
        before_arm: Callable[[Arm], None] | None = None,
    ) -> Mapping[Arm, SutOutcome]:
        """Run OFF then ON serially through one exact network and relay URL."""

        if set(paths) != {Arm.OFF, Arm.ON} or set(prompts) != {Arm.OFF, Arm.ON} or set(stores) != {Arm.OFF, Arm.ON}:
            raise UnsafeSutConfiguration("A treatment pair requires exactly OFF and ON inputs")
        if (
            len({os.path.realpath(value.workspace) for value in paths.values()}) != 2
            or len({os.path.realpath(value.runtime) for value in paths.values()}) != 2
        ):
            raise UnsafeSutConfiguration("OFF and ON must use distinct fresh roots")
        # Attribute source/network/relay preflight failures to the treatment runtime,
        # not to the already-completed Gold evaluation.  The callback remains exactly
        # once per arm, but OFF begins before shared Docker control-plane work.
        if before_arm is not None:
            before_arm(Arm.OFF)
        source_provenance = self._verify_source(config)
        if config.tokensflow_enabled:
            for arm in (Arm.OFF, Arm.ON):
                self._validate_tokensflow_source(config, paths[arm])
        with self._run_network(config, config.source_checkout) as (network, relay_url):
            outcomes: dict[Arm, SutOutcome] = {}
            for arm in (Arm.OFF, Arm.ON):
                if before_arm is not None and arm is Arm.ON:
                    before_arm(arm)
                outcomes[arm] = self._execute_arm(
                    config,
                    arm,
                    paths[arm],
                    prompts[arm],
                    stores[arm],
                    network,
                    relay_url,
                    source_provenance,
                )
            return outcomes

    @contextmanager
    def _run_network(self, config: SutConfig, cwd: Path) -> Iterator[tuple[str, str | None]]:
        network = f"powercontext-eval-{config.run_id}"
        relay = self._relay_factory() if config.proxy is not None else None
        network_created = False
        preserve_for_diagnosis = False
        try:
            # Docker's control socket becomes unreliable when many task threads create
            # bridges and start relays simultaneously.  Take the shared Docker budget
            # before the network lock so long-running attached operations cannot leave
            # every other pair queued behind a lock holder that is itself waiting for
            # admission. OFF/ON execution remains fully parallel.
            with docker_pressure.heavy_operation(), _DOCKER_NETWORK_CONTROL_LOCK:
                self._create_network(config, network, cwd)
                network_created = True
                relay_url = None
                if relay is not None:
                    gateway = self.network_gateway(network, cwd)
                    assert config.proxy is not None
                    relay_url = relay.start(gateway, config.proxy)
            yield network, relay_url
        except BaseException:
            preserve_for_diagnosis = network_created
            raise
        finally:
            with docker_pressure.heavy_operation(), _DOCKER_NETWORK_CONTROL_LOCK:
                try:
                    if relay is not None:
                        relay.stop()
                finally:
                    remove_network = network_created and (
                        not preserve_for_diagnosis
                        or self._network_is_exact_owned_and_empty(network, config.run_id, cwd)
                    )
                    if remove_network:
                        self._docker.run(
                            ("docker", "network", "rm", network),
                            cwd=cwd,
                            timeout=30,
                            check=False,
                        )

    def _create_network(self, config: SutConfig, network: str, cwd: Path) -> None:
        last_error: CommandError | None = None
        for subnet in _docker_network_subnet_candidates(config.run_id, config.docker_network_pool):
            gateway = str(ipaddress.ip_network(subnet).network_address + 1)
            command = (
                "docker",
                "network",
                "create",
                *(("--internal",) if config.proxy is not None else ()),
                "--subnet",
                subnet,
                "--gateway",
                gateway,
                "--label",
                f"powercontext-eval.run={config.run_id}",
                network,
            )
            for attempt in range(_DOCKER_NETWORK_CREATE_ATTEMPTS):
                try:
                    self._docker.run(command, cwd=cwd, timeout=30)
                    return
                except CommandError as error:
                    last_error = error
                    if self._network_has_exact_owner(network, config.run_id, cwd):
                        return
                    if _is_docker_network_subnet_collision(error):
                        break
                    if attempt + 1 == _DOCKER_NETWORK_CREATE_ATTEMPTS:
                        raise
                    self._sleeper(_DOCKER_NETWORK_CREATE_RETRY_SECONDS * (attempt + 1))
        assert last_error is not None
        raise last_error

    def _network_has_exact_owner(self, network: str, run_id: str, cwd: Path) -> bool:
        try:
            result = self._docker.run(
                (
                    "docker",
                    "network",
                    "inspect",
                    "--format",
                    '{{ index .Labels "powercontext-eval.run" }}',
                    network,
                ),
                cwd=cwd,
                timeout=30,
                check=False,
            )
        except (CommandError, OSError):
            return False
        return result.returncode == 0 and result.stdout.strip() == run_id

    def _network_is_exact_owned_and_empty(self, network: str, run_id: str, cwd: Path) -> bool:
        try:
            result = self._docker.run(
                (
                    "docker",
                    "network",
                    "inspect",
                    "--format",
                    '{{ index .Labels "powercontext-eval.run" }} {{ len .Containers }}',
                    network,
                ),
                cwd=cwd,
                timeout=30,
                check=False,
            )
        except (CommandError, OSError):
            return False
        return result.returncode == 0 and result.stdout.strip() == f"{run_id} 0"

    def _execute_arm(
        self,
        config: SutConfig,
        arm: Arm,
        paths: ArmPaths,
        prompt: bytes,
        store: ArtifactStore,
        network: str,
        relay_url: str | None,
        source_provenance: SourceProvenance,
    ) -> SutOutcome:
        container = f"{network}-{arm.value}"
        container_started = False
        tokensflow_egress_attached = False
        handed_off = False
        preserve_after_drain_failure = False
        preserve_after_infrastructure_failure = False
        tokensflow_wrapper_staged = False
        tokensflow_binary_staged = False
        tokensflow_environment = tokensflow_runtime_environment() if config.tokensflow_enabled else {}
        tokensflow_environment_values = tuple(
            dict.fromkeys(value for value in tokensflow_environment.values() if value)
        )
        tokensflow_command_secrets = tokensflow_environment_values
        if config.tokensflow_enabled:
            tokensflow_command_secrets = (
                tokensflow_secret_variants(paths.tokensflow_home / ".tokensflow/credentials.json")
                + tokensflow_environment_values
            )
        credential_variants = auth_secret_variants(paths.auth_source) + tokensflow_command_secrets
        tokensflow: TokensFlowEvidence | None = None
        tokensflow_daemon: TokensFlowDaemonHandle | None = None
        try:
            paths.prepare()
            paths.copy_auth()
            paths.copy_codex_config()
            self._stage_recorder(config, paths)
            if config.tokensflow_enabled:
                self._stage_tokensflow_wrapper(paths)
                tokensflow_wrapper_staged = True
                self._stage_tokensflow_binary(config, paths)
                tokensflow_binary_staged = True
            self._initialize_workspace(config, arm, paths)
            self._prewarm(config, arm, paths, network, relay_url)
            self._start_container(
                config,
                arm,
                paths,
                network,
                container,
                relay_url,
                tokensflow_environment,
                tokensflow_command_secrets,
            )
            container_started = True
            self._verify_codex_version(container, paths, store)
            self._readiness(container, paths, store)
            plugin = self._plugin_list(container, paths)
            if config.tokensflow_enabled:
                self._attach_tokensflow_egress(config, container, paths)
                tokensflow_egress_attached = True
                tokensflow = self._tokensflow_identity_gate(
                    config,
                    container,
                    paths,
                    tokensflow_environment,
                    tokensflow_command_secrets,
                )
                tokensflow_daemon = self._start_tokensflow_daemon(
                    config,
                    container,
                    paths,
                    tokensflow_environment,
                    tokensflow_command_secrets,
                )
                tokensflow = replace(
                    tokensflow,
                    daemon_started=True,
                    daemon_started_at=datetime.now(UTC).isoformat(),
                )
            codex = CodexRunner(_DockerExecRunner(self._docker, container)).run(
                CodexInvocation(
                    arm,
                    inside_disposable_container=True,
                    executable=_CONTAINER_CODEX,
                    model=config.model,
                    reasoning_effort=config.reasoning_effort,
                    recorder_python="/runtime/pc-env/bin/python",
                    recorder_script=_CONTAINER_RECORDER,
                    recorder_sidecar="/runtime/pc-home/codex-observed.jsonl",
                ),
                prompt=prompt,
                cwd=paths.workspace,
                store=store,
                timeout=config.codex_timeout,
                env={
                    **runtime_proxy_environment(relay_url, config.extra_no_proxy_hosts),
                    "POWERCONTEXT_HOME": "/runtime/pc-home",
                    "POWERCONTEXT_CODEX_SCOPE_ID": f"eval:{config.run_id}:{arm.value}",
                    "POWERCONTEXT_EVAL_TRACE_PATH": "/runtime/pc-home/evaluation-injections.jsonl",
                    "UV_PROJECT_ENVIRONMENT": "/runtime/plugin-env",
                    "UV_CACHE_DIR": "/runtime/uv-cache",
                    "UV_PYTHON_INSTALL_DIR": _CONTAINER_UV_PYTHON_INSTALL_DIR,
                    "UV_OFFLINE": "1",
                },
                secrets=credential_variants,
                scan_output_secrets=False,
            )
            if config.tokensflow_enabled and config.finalization_registrar is None:
                assert tokensflow is not None and tokensflow_daemon is not None
                tokensflow_drain_failed = False
                try:
                    tokensflow = self._drain_tokensflow(
                        container,
                        paths,
                        tokensflow_daemon,
                        tokensflow,
                        tokensflow_environment,
                        tokensflow_command_secrets,
                    )
                except TokensFlowInfrastructureError:
                    preserve_after_drain_failure = True
                    tokensflow_drain_failed = True
                if tokensflow_drain_failed:
                    self._write_tokensflow_recovery_marker(paths, "tokensflow_drain_failed")
                    raise TokensFlowInfrastructureError("TokensFlow drain failed") from None
            tokensflow_provenance: Path | None = None
            if tokensflow is not None:
                tokensflow_provenance = store.write_json("tokensflow/provenance.json", tokensflow.as_dict())
            _retain_private_trace(
                paths.pc_home / "codex-observed.jsonl",
                store,
                "context/codex-observed.jsonl",
                required=True,
            )
            _retain_private_trace(
                paths.pc_home / "evaluation-injections.jsonl",
                store,
                "context/powercontext-injections.jsonl",
                required=False,
            )
            evidence = self._evidence(config, arm, container, paths, plugin)
            if plugin != (PLUGIN_ID, source_provenance.plugin_version):
                raise InvalidTreatment("Isolated Codex home does not contain the exact expected plugin")
            validate_treatment(
                arm,
                config.run_id,
                evidence,
                expected_plugin_version=source_provenance.plugin_version,
                expected_checkout_sha=config.plugin_checkout_sha,
            )
            logs = self._docker.run(
                ("docker", "logs", container),
                cwd=paths.runtime,
                timeout=30,
                check=False,
            )
            server_log = logs.stdout + logs.stderr
            _reject_retained_secrets(server_log.encode("utf-8"), credential_variants)
            store.write_text("powercontext/server.log", server_log)
            treatment_bytes = (
                json.dumps(evidence.as_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
            ).encode("utf-8")
            _reject_retained_secrets(treatment_bytes, credential_variants)
            store.write_bytes("powercontext/treatment.json", treatment_bytes)
            provenance_bytes = (
                json.dumps(source_provenance.as_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
            ).encode("utf-8")
            _reject_retained_secrets(provenance_bytes, credential_variants)
            store.write_bytes("powercontext/provenance.json", provenance_bytes)
            patch = self._docker.run(
                ("git", "diff", "--binary", "--full-index", "HEAD", "--"),
                cwd=paths.workspace,
                timeout=120,
            ).stdout
            store.write_text("workspace.patch", patch)
            if config.tokensflow_enabled and config.finalization_registrar is not None:
                assert tokensflow_provenance is not None
                assert tokensflow_daemon is not None
                assert config.tokensflow_egress_network is not None
                provenance_raw = tokensflow_provenance.read_bytes()
                descriptor = TokensFlowFinalizationDescriptor(
                    arm=arm,
                    run_id=config.run_id,
                    container_name=container,
                    runtime=paths.runtime,
                    wrapper=paths.runtime.parent / "evaluation-control/tokensflow-wrapper",
                    egress_network=config.tokensflow_egress_network,
                    daemon_pid_file=tokensflow_daemon.container_pid_file,
                    evidence_sha256=hashlib.sha256(provenance_raw).hexdigest(),
                    evidence_bytes=len(provenance_raw),
                )
                try:
                    config.finalization_registrar(descriptor)
                except Exception:  # noqa: BLE001 - ownership transfers only after the durable callback returns
                    raise TokensFlowInfrastructureError("TokensFlow finalization handoff failed") from None
                handed_off = True
                try:
                    self._docker.run(
                        ("docker", "network", "disconnect", network, container),
                        cwd=paths.runtime,
                        timeout=30,
                        check=False,
                    )
                except (CommandError, OSError):
                    pass
            return SutOutcome(codex, evidence, tokensflow, tokensflow_daemon)
        except BaseException:
            preserve_after_infrastructure_failure = container_started
            if (
                config.tokensflow_enabled
                and preserve_after_infrastructure_failure
                and not (paths.runtime / "tokensflow-recovery.json").exists()
            ):
                self._write_tokensflow_recovery_marker(paths, "infrastructure_failure_retained")
            raise
        finally:
            if not handed_off:
                preserve_for_diagnosis = preserve_after_drain_failure or preserve_after_infrastructure_failure
                try:
                    if tokensflow_egress_attached:
                        self._detach_tokensflow_egress(config, container, paths)
                finally:
                    container_removed = not container_started
                    if container_started and not preserve_for_diagnosis:
                        container_removed = self._remove_container_for_cleanup(container, paths)
                        if not container_removed and config.tokensflow_enabled:
                            self._write_tokensflow_recovery_marker(paths, "tokensflow_container_cleanup_failed")
                    if not preserve_for_diagnosis and container_removed:
                        if tokensflow_binary_staged:
                            self._cleanup_tokensflow_binary(paths)
                        if tokensflow_wrapper_staged:
                            self._cleanup_tokensflow_wrapper(paths)
                    if container_started and not preserve_for_diagnosis and not container_removed:
                        if config.tokensflow_enabled:
                            raise TokensFlowInfrastructureError("TokensFlow container cleanup failed") from None
                        raise UnsafeSutConfiguration("Task container cleanup failed") from None

    @staticmethod
    def _validate_tokensflow_source(config: SutConfig, paths: ArmPaths) -> None:
        if paths.tokensflow_home is None:
            raise TokensFlowInfrastructureError("TokensFlow inputs must be configured")
        if config.tokensflow_binary is None:
            raise TokensFlowInfrastructureError("TokensFlow inputs must be configured")
        try:
            _tool_directory_mount(
                config.tokensflow_binary,
                "/tools/tokensflow-dir",
                expected_name="tokensflow",
                require_executable=True,
            )
        except UnsafeSutConfiguration:
            raise TokensFlowInfrastructureError("TokensFlow binary validation failed") from None

    @staticmethod
    def _validate_tokensflow_inputs(paths: ArmPaths) -> tuple[Path, Path]:
        if paths.tokensflow_home is None:
            raise TokensFlowInfrastructureError("TokensFlow inputs must be configured")
        snapshot = paths.runtime.parent / "evaluation-control" / "tokensflow-binary" / "tokensflow"
        try:
            _tool_directory_mount(
                snapshot,
                "/tools/tokensflow-dir",
                expected_name="tokensflow",
                require_executable=True,
            )
        except UnsafeSutConfiguration:
            raise TokensFlowInfrastructureError("TokensFlow binary validation failed") from None
        return snapshot, paths.tokensflow_home

    def _tokensflow_egress_is_attached(self, config: SutConfig, container: str, paths: ArmPaths) -> bool:
        if config.tokensflow_egress_network is None:
            raise TokensFlowInfrastructureError("TokensFlow inputs must be configured")
        template = (
            '{{if index .NetworkSettings.Networks "' + config.tokensflow_egress_network + '"}}true{{else}}false{{end}}'
        )
        try:
            result = self._docker.run(
                ("docker", "inspect", "--format", template, container),
                cwd=paths.runtime,
                timeout=30,
            )
        except (CommandError, OSError):
            raise TokensFlowInfrastructureError("TokensFlow egress network inspection failed") from None
        attached = result.stdout.strip()
        if attached not in {"true", "false"}:
            raise TokensFlowInfrastructureError("TokensFlow egress network inspection failed")
        return attached == "true"

    def _attach_tokensflow_egress(self, config: SutConfig, container: str, paths: ArmPaths) -> None:
        if config.tokensflow_egress_network is None:
            raise TokensFlowInfrastructureError("TokensFlow inputs must be configured")
        try:
            if not self._tokensflow_egress_is_attached(config, container, paths):
                self._docker.run(
                    ("docker", "network", "connect", config.tokensflow_egress_network, container),
                    cwd=paths.runtime,
                    timeout=30,
                )
            if not self._tokensflow_egress_is_attached(config, container, paths):
                raise TokensFlowInfrastructureError("TokensFlow egress network attachment failed")
        except (CommandError, OSError, TokensFlowInfrastructureError):
            self._detach_tokensflow_egress(config, container, paths)
            raise TokensFlowInfrastructureError("TokensFlow egress network attachment failed") from None

    def _detach_tokensflow_egress(self, config: SutConfig, container: str, paths: ArmPaths) -> None:
        if config.tokensflow_egress_network is None:
            return
        try:
            self._docker.run(
                ("docker", "network", "disconnect", config.tokensflow_egress_network, container),
                cwd=paths.runtime,
                timeout=30,
                check=False,
            )
        except (CommandError, OSError):
            pass

    def _capture_tokensflow_output(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        environment: Mapping[str, str] | None = None,
        timeout: float = 30,
        secrets: Sequence[str] = (),
    ) -> bytes:
        for attempt in range(_TOKENSFLOW_RETRY_ATTEMPTS):
            output, returncode = self._capture_tokensflow_result(
                argv,
                cwd=cwd,
                environment=environment,
                timeout=timeout,
                secrets=secrets,
            )
            if returncode == 0:
                return output
            if attempt < _TOKENSFLOW_RETRY_ATTEMPTS - 1:
                time.sleep(1)
        raise TokensFlowInfrastructureError("TokensFlow command failed")

    def _capture_tokensflow_result(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        environment: Mapping[str, str] | None = None,
        timeout: float = 30,
        secrets: Sequence[str] = (),
    ) -> tuple[bytes, int]:
        result: CommandResult | None = None
        output = b""
        try:
            with tempfile.TemporaryFile() as sink:
                result = self._docker.run(
                    argv,
                    cwd=cwd,
                    timeout=timeout,
                    env=environment,
                    check=False,
                    secrets=secrets,
                    stdout_sink=sink,
                )
                sink.seek(0)
                output = sink.read()
                if result.stderr:
                    output += result.stderr.encode("utf-8")
        except (CommandError, OSError):
            pass
        if result is None:
            raise TokensFlowInfrastructureError("TokensFlow command failed")
        return output, result.returncode

    def _tokensflow_identity_gate(
        self,
        config: SutConfig,
        container: str,
        paths: ArmPaths,
        runtime_environment: Mapping[str, str],
        command_secrets: Sequence[str],
    ) -> TokensFlowEvidence:
        tokensflow_binary, tokensflow_home = self._validate_tokensflow_inputs(paths)
        host_environment = {
            **runtime_environment,
            **direct_egress_environment(),
            "HOME": os.fspath(tokensflow_home),
        }
        container_environment = {
            **runtime_environment,
            **direct_egress_environment(),
        }
        host_version = parse_tokensflow_version(
            self._capture_tokensflow_output(
                (os.fspath(tokensflow_binary), "--version"),
                cwd=paths.runtime,
                environment=host_environment,
                secrets=command_secrets,
            )
        )
        container_version = parse_tokensflow_version(
            self._capture_tokensflow_output(
                (
                    "docker",
                    "exec",
                    *_docker_env_args(container_environment),
                    container,
                    _CONTAINER_TOKENSFLOW,
                    "--version",
                ),
                cwd=paths.runtime,
                secrets=command_secrets,
            )
        )
        host_identity = self._capture_tokensflow_output(
            (os.fspath(tokensflow_binary), "whoami"),
            cwd=paths.runtime,
            environment=host_environment,
            secrets=command_secrets,
        )
        container_identity = self._capture_tokensflow_output(
            (
                "docker",
                "exec",
                *_docker_env_args(container_environment),
                container,
                _CONTAINER_TOKENSFLOW,
                "whoami",
            ),
            cwd=paths.runtime,
            secrets=command_secrets,
        )
        return matched_tokensflow_evidence(
            host_version=host_version,
            container_version=container_version,
            host_identity=host_identity,
            container_identity=container_identity,
        )

    def _start_tokensflow_daemon(
        self,
        config: SutConfig,
        container: str,
        paths: ArmPaths,
        runtime_environment: Mapping[str, str],
        command_secrets: Sequence[str],
    ) -> TokensFlowDaemonHandle:
        _, tokensflow_home = self._validate_tokensflow_inputs(paths)
        state = "/root/.local/share/tokensflow"
        container_pid_file = f"{state}/evaluation-daemon.pid"
        container_log_file = f"{state}/evaluation-daemon.log"
        environment = {
            **runtime_environment,
            **direct_egress_environment(),
        }
        script = (
            f'umask 077; echo "$$" > {container_pid_file}; '
            f"exec {_CONTAINER_TOKENSFLOW} daemon >> {container_log_file} 2>&1"
        )
        start_failed = False
        try:
            self._docker.run(
                (
                    "docker",
                    "exec",
                    "-d",
                    *_docker_env_args(environment),
                    container,
                    "/bin/sh",
                    "-c",
                    script,
                ),
                cwd=paths.runtime,
                timeout=30,
                secrets=command_secrets,
            )
        except (CommandError, OSError):
            start_failed = True
        if start_failed:
            raise TokensFlowInfrastructureError("TokensFlow daemon failed to start")
        readiness_script = (
            f"test -s {container_pid_file} || exit 1; "
            f'pid="$(cat {container_pid_file})"; '
            'case "$pid" in ""|*[!0-9]*) exit 1;; esac; '
            f'test "$(readlink "/proc/$pid/exe")" = "{_CONTAINER_TOKENSFLOW}" || exit 1; '
            'tr "\\000" "\\n" < "/proc/$pid/cmdline" | grep -Fqx daemon'
        )
        readiness_command = (
            "docker",
            "exec",
            *_docker_env_args(environment),
            container,
            "/bin/sh",
            "-c",
            readiness_script,
        )
        deadline = self._clock() + 5
        while True:
            try:
                readiness = self._docker.run(
                    readiness_command,
                    cwd=paths.runtime,
                    timeout=min(1.0, max(0.1, deadline - self._clock())),
                    check=False,
                    secrets=command_secrets,
                )
            except (CommandError, OSError):
                readiness = None
            if readiness is not None and readiness.returncode == 0:
                break
            if self._clock() >= deadline:
                raise TokensFlowInfrastructureError("TokensFlow daemon failed to start")
            self._sleeper(0.05)
        host_state = tokensflow_home / ".local/share/tokensflow"
        return TokensFlowDaemonHandle(
            pid_file=host_state / "evaluation-daemon.pid",
            log_file=host_state / "evaluation-daemon.log",
            container_pid_file=container_pid_file,
            container_log_file=container_log_file,
        )

    def _drain_tokensflow(
        self,
        container: str,
        paths: ArmPaths,
        daemon: TokensFlowDaemonHandle,
        evidence: TokensFlowEvidence,
        runtime_environment: Mapping[str, str],
        command_secrets: Sequence[str],
    ) -> TokensFlowEvidence:
        deadline = DrainDeadline(clock=self._clock)
        environment = {
            **runtime_environment,
            **direct_egress_environment(),
        }
        self._stop_tokensflow_daemon(
            container,
            paths,
            daemon,
            environment,
            command_secrets,
            deadline,
        )
        upload = (
            "docker",
            "exec",
            *_docker_env_args(environment),
            container,
            _CONTAINER_TOKENSFLOW,
            "upload",
            "--all",
        )
        self._capture_tokensflow_output(
            upload,
            cwd=paths.runtime,
            timeout=deadline.remaining(),
            secrets=command_secrets,
        )
        status, doctor_rc = self._capture_tokensflow_result(
            (
                "docker",
                "exec",
                *_docker_env_args(environment),
                container,
                _CONTAINER_TOKENSFLOW,
                "doctor",
            ),
            cwd=paths.runtime,
            timeout=deadline.remaining(),
            secrets=command_secrets,
        )
        negative_detected = tokensflow_queue_negative_detected(status)
        if not tokensflow_queue_caught_up(status):
            raise TokensFlowInfrastructureError("TokensFlow queue did not catch up")
        duration = 60.0 - deadline.remaining()
        return replace(
            evidence,
            daemon_stopped=True,
            upload_all_succeeded=True,
            queue_caught_up=True,
            doctor_rc=doctor_rc,
            negative_detected=negative_detected,
            drain_duration_seconds=round(duration, 6),
        )

    def _stop_tokensflow_daemon(
        self,
        container: str,
        paths: ArmPaths,
        daemon: TokensFlowDaemonHandle,
        environment: Mapping[str, str],
        command_secrets: Sequence[str],
        deadline: DrainDeadline,
    ) -> None:
        initial_probe = (
            f"# tokensflow-stop-initial-probe\n"
            f"test -s {daemon.container_pid_file} || exit 10\n"
            f'pid="$(cat {daemon.container_pid_file})" || exit 10\n'
            'case "$pid" in ""|0|*[!0-9]*) exit 10;; esac\n'
            'test -e "/proc/$pid" || exit 10\n'
            'exe="$(readlink "/proc/$pid/exe" 2>/dev/null)" || '
            '{ test ! -e "/proc/$pid" && exit 20; exit 10; }\n'
            f'test "$exe" = "{_CONTAINER_TOKENSFLOW}" || exit 10\n'
            'cmdline="$(tr "\\000" "\\n" 2>/dev/null < "/proc/$pid/cmdline")" || '
            '{ test ! -e "/proc/$pid" && exit 20; exit 10; }\n'
            'printf "%s\\n" "$cmdline" | grep -Fqx daemon || exit 10\n'
            'printf "%s\\n" "$pid"'
        )
        initial = self._run_tokensflow_stop_command(
            (
                "docker",
                "exec",
                *_docker_env_args(environment),
                container,
                "/bin/sh",
                "-c",
                initial_probe,
            ),
            paths=paths,
            command_secrets=command_secrets,
            deadline=deadline,
        )
        if initial.returncode == 20:
            return
        pid = initial.stdout.strip()
        if initial.returncode != 0 or re.fullmatch(r"[1-9][0-9]*", pid) is None:
            raise TokensFlowInfrastructureError("TokensFlow daemon failed to stop")

        term_script = f'# tokensflow-stop-term {daemon.container_pid_file}\nkill -TERM "$1"'
        terminated = self._run_tokensflow_stop_command(
            (
                "docker",
                "exec",
                *_docker_env_args(environment),
                container,
                "/bin/sh",
                "-c",
                term_script,
                "tokensflow-stop-term",
                pid,
            ),
            paths=paths,
            command_secrets=command_secrets,
            deadline=deadline,
        )
        if terminated.returncode != 0:
            absent = self._run_tokensflow_stop_command(
                (
                    "docker",
                    "exec",
                    *_docker_env_args(environment),
                    container,
                    "/bin/sh",
                    "-c",
                    '# tokensflow-stop-absent\ntest ! -e "/proc/$1"',
                    "tokensflow-stop-absent",
                    pid,
                ),
                paths=paths,
                command_secrets=command_secrets,
                deadline=deadline,
            )
            if absent.returncode == 0:
                return
            raise TokensFlowInfrastructureError("TokensFlow daemon failed to stop")

        poll_script = (
            "# tokensflow-stop-poll\n"
            'test -e "/proc/$1" || exit 0\n'
            'exe="$(readlink "/proc/$1/exe" 2>/dev/null)" || exit 0\n'
            f'test "$exe" = "{_CONTAINER_TOKENSFLOW}" || exit 0\n'
            'cmdline="$(tr "\\000" "\\n" 2>/dev/null < "/proc/$1/cmdline")" || exit 0\n'
            'printf "%s\\n" "$cmdline" | grep -Fqx daemon || exit 0\n'
            "exit 11"
        )
        while True:
            polled = self._run_tokensflow_stop_command(
                (
                    "docker",
                    "exec",
                    *_docker_env_args(environment),
                    container,
                    "/bin/sh",
                    "-c",
                    poll_script,
                    "tokensflow-stop-poll",
                    pid,
                ),
                paths=paths,
                command_secrets=command_secrets,
                deadline=deadline,
                timeout_cap=1.0,
            )
            if polled.returncode == 0:
                return
            if polled.returncode != 11:
                raise TokensFlowInfrastructureError("TokensFlow daemon failed to stop")
            self._sleeper(min(0.05, deadline.remaining()))

    def _run_tokensflow_stop_command(
        self,
        argv: tuple[str, ...],
        *,
        paths: ArmPaths,
        command_secrets: Sequence[str],
        deadline: DrainDeadline,
        timeout_cap: float | None = None,
    ) -> CommandResult:
        timeout = deadline.remaining()
        if timeout_cap is not None:
            timeout = min(timeout_cap, timeout)
        try:
            return self._docker.run(
                argv,
                cwd=paths.runtime,
                timeout=timeout,
                check=False,
                secrets=command_secrets,
            )
        except (CommandError, OSError):
            raise TokensFlowInfrastructureError("TokensFlow daemon failed to stop") from None

    @staticmethod
    def _write_tokensflow_recovery_marker(paths: ArmPaths, reason: str) -> None:
        payloads = {
            "tokensflow_drain_failed": b'{"reason":"tokensflow_drain_failed","recovery_required":true}\n',
            "tokensflow_container_cleanup_failed": (
                b'{"reason":"tokensflow_container_cleanup_failed","recovery_required":true}\n'
            ),
            "infrastructure_failure_retained": (
                b'{"reason":"infrastructure_failure_retained","recovery_required":true}\n'
            ),
        }
        try:
            payload = payloads[reason]
        except KeyError:
            raise ValueError("unsupported TokensFlow recovery reason") from None
        marker = paths.runtime / "tokensflow-recovery.json"
        descriptor = -1
        try:
            descriptor = os.open(
                marker,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
            os.write(descriptor, payload)
            os.fchmod(descriptor, 0o600)
            os.fsync(descriptor)
        except OSError:
            raise TokensFlowInfrastructureError("TokensFlow recovery marker failed") from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def _remove_container_for_cleanup(self, container: str, paths: ArmPaths) -> bool:
        """Return true only after removal succeeds or inspect proves absence."""

        try:
            removal = self._docker.run(
                ("docker", "rm", "-f", container),
                cwd=paths.runtime,
                timeout=30,
                check=False,
            )
        except CommandTimedOut:
            return self._await_timed_out_container_removal(container, paths)
        except (CommandError, OSError):
            removal = None
        if removal is not None and removal.returncode == 0:
            return True
        try:
            inspection = self._docker.run(
                ("docker", "container", "inspect", container),
                cwd=paths.runtime,
                timeout=30,
                check=False,
            )
        except (CommandError, OSError):
            return False
        return inspection.returncode != 0 and inspection.stderr.strip() in {
            f"Error: No such container: {container}",
            f"Error response from daemon: No such container: {container}",
        }

    def _await_timed_out_container_removal(self, container: str, paths: ArmPaths) -> bool:
        """Wait for Docker's asynchronous forced stop, then finish removing the container."""

        deadline = self._clock() + _TIMED_OUT_CONTAINER_REMOVAL_SETTLE_SECONDS
        while self._clock() < deadline:
            try:
                inspection = self._docker.run(
                    (
                        "docker",
                        "container",
                        "inspect",
                        "--format={{.State.Status}}",
                        container,
                    ),
                    cwd=paths.runtime,
                    timeout=min(5.0, max(0.1, deadline - self._clock())),
                    check=False,
                )
            except (CommandError, OSError):
                return False
            if inspection.returncode != 0:
                return inspection.stderr.strip() in {
                    f"Error: No such container: {container}",
                    f"Error response from daemon: No such container: {container}",
                }
            state = inspection.stdout.strip()
            if state in {"created", "dead", "exited"}:
                try:
                    removal = self._docker.run(
                        ("docker", "rm", "-f", container),
                        cwd=paths.runtime,
                        timeout=min(30.0, max(0.1, deadline - self._clock())),
                        check=False,
                    )
                except (CommandError, OSError):
                    return False
                return removal.returncode == 0
            if state not in {"paused", "restarting", "running"}:
                return False
            remaining = deadline - self._clock()
            if remaining <= 0:
                break
            self._sleeper(min(_TIMED_OUT_CONTAINER_REMOVAL_POLL_SECONDS, remaining))
        return False

    def _initialize_workspace(self, config: SutConfig, arm: Arm, paths: ArmPaths) -> None:
        # Concurrent image extraction is another Docker control-plane and metadata
        # hotspot. Share the process-wide daemon budget while scheduling 20 tasks.
        with docker_pressure.heavy_operation():
            self._initialize_workspace_with_docker(config, arm, paths)

    def _initialize_workspace_with_docker(self, config: SutConfig, arm: Arm, paths: ArmPaths) -> None:
        name = f"powercontext-eval-{config.run_id}-{arm.value}-init"
        created = False
        try:
            self._docker.run(
                (
                    "docker",
                    "create",
                    "--name",
                    name,
                    "--label",
                    f"powercontext-eval.run={config.run_id}",
                    config.task_image,
                ),
                cwd=paths.runtime,
                timeout=60,
            )
            created = True
            try:
                self._docker.run(
                    ("docker", "cp", f"{name}:/app/.", os.fspath(paths.workspace)),
                    cwd=paths.runtime,
                    timeout=300,
                )
            except CommandFailed as error:
                if _INVALID_DOCKER_COPY_SYMLINK.fullmatch(error.result.stderr.strip()) is None:
                    raise
                shutil.rmtree(paths.workspace)
                paths.workspace.mkdir(mode=0o700)
                self._docker.run(
                    (
                        "docker",
                        "run",
                        "--rm",
                        "--network",
                        "none",
                        "--cpus",
                        config.limits.cpus,
                        "--memory",
                        config.limits.memory,
                        "--pids-limit",
                        str(config.limits.pids),
                        "--mount",
                        f"type=bind,src={paths.workspace},dst=/workspace",
                        "--entrypoint",
                        "/bin/cp",
                        config.task_image,
                        "--archive",
                        "--no-preserve=ownership",
                        "/app/.",
                        "/workspace",
                    ),
                    cwd=paths.runtime,
                    timeout=300,
                )
        finally:
            if created:
                self._docker.run(("docker", "rm", "-f", name), cwd=paths.runtime, timeout=30, check=False)

    @staticmethod
    def _stage_recorder(config: SutConfig, paths: ArmPaths) -> None:
        """Copy the evaluator-owned recorder into a fresh private control directory."""

        control = paths.runtime / "evaluation-control"
        control.mkdir(mode=0o700)
        destination = control / "record_codex_jsonl.py"
        source_fd = os.open(
            config.recorder_script,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
        destination_fd = -1
        try:
            source_metadata = os.fstat(source_fd)
            if not stat.S_ISREG(source_metadata.st_mode):
                raise UnsafeSutConfiguration("Codex recorder script must be a regular file")
            destination_fd = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                0o400,
            )
            while chunk := os.read(source_fd, 64 * 1024):
                view = memoryview(chunk)
                written = 0
                while written < len(view):
                    count = os.write(destination_fd, view[written:])
                    if count <= 0:
                        raise OSError("Codex recorder copy made no progress")
                    written += count
            os.fchmod(destination_fd, 0o400)
            os.fsync(destination_fd)
        except OSError as error:
            raise UnsafeSutConfiguration("Codex recorder script cannot be staged safely") from error
        finally:
            os.close(source_fd)
            if destination_fd >= 0:
                os.close(destination_fd)

    @staticmethod
    def _stage_tokensflow_wrapper(paths: ArmPaths) -> None:
        """Stage the evaluator-owned wrapper outside container-writable mounts."""

        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        root_fd = -1
        control_fd = -1
        wrapper_fd = -1
        destination_fd = -1
        control_created = False
        wrapper_created = False
        file_created = False
        try:
            root_fd = os.open(paths.runtime.parent, directory_flags)
            os.mkdir("evaluation-control", mode=0o700, dir_fd=root_fd)
            control_created = True
            control_fd = os.open("evaluation-control", directory_flags, dir_fd=root_fd)
            os.mkdir("tokensflow-wrapper", mode=0o700, dir_fd=control_fd)
            wrapper_created = True
            wrapper_fd = os.open("tokensflow-wrapper", directory_flags, dir_fd=control_fd)
            destination_fd = os.open(
                "tokensflow",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                0o555,
                dir_fd=wrapper_fd,
            )
            file_created = True
            view = memoryview(_TOKENSFLOW_WRAPPER)
            written = 0
            while written < len(view):
                count = os.write(destination_fd, view[written:])
                if count <= 0:
                    raise OSError("TokensFlow wrapper staging made no progress")
                written += count
            os.fchmod(destination_fd, 0o555)
            os.fsync(destination_fd)
            os.close(destination_fd)
            destination_fd = -1
            os.fchmod(wrapper_fd, 0o555)
            os.fsync(wrapper_fd)
            os.fchmod(control_fd, 0o555)
            os.fsync(control_fd)
        except OSError as error:
            try:
                if destination_fd >= 0:
                    os.close(destination_fd)
                    destination_fd = -1
                if wrapper_fd >= 0:
                    os.fchmod(wrapper_fd, 0o700)
                    if file_created:
                        os.unlink("tokensflow", dir_fd=wrapper_fd)
                if control_fd >= 0:
                    os.fchmod(control_fd, 0o700)
                    if (
                        wrapper_created
                        and wrapper_fd >= 0
                        and _directory_entry_matches(control_fd, "tokensflow-wrapper", wrapper_fd)
                    ):
                        os.rmdir("tokensflow-wrapper", dir_fd=control_fd)
                if (
                    root_fd >= 0
                    and control_created
                    and control_fd >= 0
                    and _directory_entry_matches(root_fd, "evaluation-control", control_fd)
                ):
                    os.rmdir("evaluation-control", dir_fd=root_fd)
            except OSError:
                pass
            raise UnsafeSutConfiguration("TokensFlow wrapper cannot be staged safely") from error
        finally:
            if destination_fd >= 0:
                os.close(destination_fd)
            if wrapper_fd >= 0:
                os.close(wrapper_fd)
            if control_fd >= 0:
                os.close(control_fd)
            if root_fd >= 0:
                os.close(root_fd)

    @staticmethod
    def _stage_tokensflow_binary(config: SutConfig, paths: ArmPaths) -> Path:
        """Snapshot the mutable host install behind the evaluator-owned wrapper."""

        source = config.tokensflow_binary
        if source is None:
            raise TokensFlowInfrastructureError("TokensFlow binary snapshot failed")
        raw = os.fspath(source)
        if not source.is_absolute() or source.name != "tokensflow" or "\0" in raw or ".." in source.parts:
            raise TokensFlowInfrastructureError("TokensFlow binary snapshot failed")
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        file_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        root_fd = -1
        control_fd = -1
        snapshot_fd = -1
        destination_fd = -1
        source_fd = -1
        snapshot_created = False
        file_created = False
        try:
            source_fd = os.open(source, file_flags)
            source_metadata = os.fstat(source_fd)
            if not stat.S_ISREG(source_metadata.st_mode) or source_metadata.st_mode & 0o111 == 0:
                raise OSError("TokensFlow source is not an executable regular file")
            root_fd = os.open(paths.runtime.parent, directory_flags)
            control_fd = os.open("evaluation-control", directory_flags, dir_fd=root_fd)
            control_metadata = os.fstat(control_fd)
            if not stat.S_ISDIR(control_metadata.st_mode) or stat.S_IMODE(control_metadata.st_mode) != 0o555:
                raise OSError("TokensFlow control directory is unsafe")
            os.fchmod(control_fd, 0o700)
            os.mkdir("tokensflow-binary", mode=0o700, dir_fd=control_fd)
            snapshot_created = True
            snapshot_fd = os.open("tokensflow-binary", directory_flags, dir_fd=control_fd)
            destination_fd = os.open(
                "tokensflow",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                0o555,
                dir_fd=snapshot_fd,
            )
            file_created = True
            while chunk := os.read(source_fd, 64 * 1024):
                view = memoryview(chunk)
                written = 0
                while written < len(view):
                    count = os.write(destination_fd, view[written:])
                    if count <= 0:
                        raise OSError("TokensFlow binary snapshot made no progress")
                    written += count
            os.fchmod(destination_fd, 0o555)
            os.fsync(destination_fd)
            os.close(destination_fd)
            destination_fd = -1
            os.fchmod(snapshot_fd, 0o555)
            os.fsync(snapshot_fd)
            os.fchmod(control_fd, 0o555)
            os.fsync(control_fd)
        except OSError:
            try:
                if destination_fd >= 0:
                    os.close(destination_fd)
                    destination_fd = -1
                if snapshot_fd >= 0:
                    os.fchmod(snapshot_fd, 0o700)
                    if file_created:
                        os.unlink("tokensflow", dir_fd=snapshot_fd)
                if control_fd >= 0:
                    os.fchmod(control_fd, 0o700)
                    if snapshot_created:
                        os.rmdir("tokensflow-binary", dir_fd=control_fd)
                    os.fchmod(control_fd, 0o555)
            except OSError:
                pass
            raise TokensFlowInfrastructureError("TokensFlow binary snapshot failed") from None
        finally:
            if destination_fd >= 0:
                os.close(destination_fd)
            if snapshot_fd >= 0:
                os.close(snapshot_fd)
            if control_fd >= 0:
                os.close(control_fd)
            if root_fd >= 0:
                os.close(root_fd)
            if source_fd >= 0:
                os.close(source_fd)
        return paths.runtime.parent / "evaluation-control" / "tokensflow-binary" / "tokensflow"

    @staticmethod
    def _cleanup_tokensflow_binary(paths: ArmPaths) -> None:
        """Remove only the exact evaluator-owned TokensFlow snapshot."""

        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        read_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        root_fd = -1
        control_fd = -1
        snapshot_fd = -1
        binary_fd = -1
        try:
            root_fd = os.open(paths.runtime.parent, directory_flags)
            try:
                control_fd = os.open("evaluation-control", directory_flags, dir_fd=root_fd)
                snapshot_fd = os.open("tokensflow-binary", directory_flags, dir_fd=control_fd)
            except FileNotFoundError:
                return
            binary_fd = os.open("tokensflow", read_flags, dir_fd=snapshot_fd)
            control_metadata = os.fstat(control_fd)
            snapshot_metadata = os.fstat(snapshot_fd)
            binary_metadata = os.fstat(binary_fd)
            if (
                not stat.S_ISDIR(control_metadata.st_mode)
                or not stat.S_ISDIR(snapshot_metadata.st_mode)
                or not stat.S_ISREG(binary_metadata.st_mode)
                or stat.S_IMODE(control_metadata.st_mode) != 0o555
                or stat.S_IMODE(snapshot_metadata.st_mode) != 0o555
                or stat.S_IMODE(binary_metadata.st_mode) != 0o555
                or set(os.listdir(snapshot_fd)) != {"tokensflow"}
            ):
                raise UnsafeSutConfiguration("TokensFlow binary cleanup path is unsafe")
            os.close(binary_fd)
            binary_fd = -1
            os.fchmod(snapshot_fd, 0o700)
            os.unlink("tokensflow", dir_fd=snapshot_fd)
            os.fchmod(control_fd, 0o700)
            os.rmdir("tokensflow-binary", dir_fd=control_fd)
            os.fchmod(control_fd, 0o555)
            os.fsync(control_fd)
        except OSError as error:
            raise UnsafeSutConfiguration("TokensFlow binary cleanup failed") from error
        finally:
            if binary_fd >= 0:
                os.close(binary_fd)
            if snapshot_fd >= 0:
                os.close(snapshot_fd)
            if control_fd >= 0:
                os.close(control_fd)
            if root_fd >= 0:
                os.close(root_fd)

    @staticmethod
    def _cleanup_tokensflow_wrapper(paths: ArmPaths) -> None:
        """Remove the per-arm wrapper after its container is gone."""

        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        read_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        root_fd = -1
        control_fd = -1
        wrapper_fd = -1
        wrapper_file_fd = -1
        try:
            root_fd = os.open(paths.runtime.parent, directory_flags)
            try:
                control_fd = os.open("evaluation-control", directory_flags, dir_fd=root_fd)
            except FileNotFoundError:
                return
            wrapper_fd = os.open("tokensflow-wrapper", directory_flags, dir_fd=control_fd)
            wrapper_file_fd = os.open("tokensflow", read_flags, dir_fd=wrapper_fd)
            control_metadata = os.fstat(control_fd)
            wrapper_metadata = os.fstat(wrapper_fd)
            file_metadata = os.fstat(wrapper_file_fd)
            if (
                not stat.S_ISDIR(control_metadata.st_mode)
                or not stat.S_ISDIR(wrapper_metadata.st_mode)
                or not stat.S_ISREG(file_metadata.st_mode)
                or stat.S_IMODE(control_metadata.st_mode) != 0o555
                or stat.S_IMODE(wrapper_metadata.st_mode) != 0o555
                or stat.S_IMODE(file_metadata.st_mode) != 0o555
                or _read_all(wrapper_file_fd) != _TOKENSFLOW_WRAPPER
            ):
                raise UnsafeSutConfiguration("TokensFlow wrapper cleanup path is unsafe")
            os.close(wrapper_file_fd)
            wrapper_file_fd = -1
            os.fchmod(wrapper_fd, 0o700)
            os.unlink("tokensflow", dir_fd=wrapper_fd)
            os.fchmod(control_fd, 0o700)
            os.rmdir("tokensflow-wrapper", dir_fd=control_fd)
            os.rmdir("evaluation-control", dir_fd=root_fd)
        except OSError as error:
            raise UnsafeSutConfiguration("TokensFlow wrapper cleanup failed") from error
        finally:
            if wrapper_file_fd >= 0:
                os.close(wrapper_file_fd)
            if wrapper_fd >= 0:
                os.close(wrapper_fd)
            if control_fd >= 0:
                os.close(control_fd)
            if root_fd >= 0:
                os.close(root_fd)

    def _prewarm(
        self,
        config: SutConfig,
        arm: Arm,
        paths: ArmPaths,
        network: str,
        relay_url: str | None,
    ) -> None:
        # Dependency installation and plugin setup start several attached Docker
        # runs. Keep the whole prewarm sequence in the shared daemon budget so
        # completed Gold jobs cannot accumulate an unbounded second wave here.
        with docker_pressure.heavy_operation():
            self._prewarm_with_docker(config, arm, paths, network, relay_url)

    def _prewarm_with_docker(
        self,
        config: SutConfig,
        arm: Arm,
        paths: ArmPaths,
        network: str,
        relay_url: str | None,
    ) -> None:
        del arm
        common_environment = {
            **runtime_proxy_environment(relay_url, config.extra_no_proxy_hosts),
            "UV_CACHE_DIR": "/runtime/uv-cache",
            "UV_PYTHON_INSTALL_DIR": _CONTAINER_UV_PYTHON_INSTALL_DIR,
        }
        command = (
            "docker",
            "run",
            "--rm",
            "--cpus",
            config.limits.cpus,
            "--memory",
            config.limits.memory,
            "--pids-limit",
            str(config.limits.pids),
            "--network",
            network,
            "--mount",
            f"type=bind,src={config.source_checkout},dst=/source,readonly",
            "--mount",
            f"type=bind,src={paths.runtime},dst=/runtime",
            "--mount",
            _tool_directory_mount(config.uv_binary, "/tools/uv-dir", expected_name="uv"),
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=512m",
            *_docker_env_args({**common_environment, "UV_PROJECT_ENVIRONMENT": "/runtime/pc-env"}),
            "--entrypoint",
            _CONTAINER_UV,
            config.task_image,
            "sync",
            "--frozen",
            "--project",
            "/source",
            "--extra",
            "server",
            "--extra",
            "cli",
        )
        self._docker.run(command, cwd=paths.runtime, timeout=900)
        self._docker.run(
            (
                "docker",
                "run",
                "--rm",
                "--cpus",
                config.limits.cpus,
                "--memory",
                config.limits.memory,
                "--pids-limit",
                str(config.limits.pids),
                "--network",
                network,
                "--mount",
                f"type=bind,src={config.source_checkout},dst=/source,readonly",
                "--mount",
                f"type=bind,src={paths.runtime},dst=/runtime",
                "--mount",
                _tool_directory_mount(config.uv_binary, "/tools/uv-dir", expected_name="uv"),
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=256m",
                *_docker_env_args({**common_environment, "UV_PROJECT_ENVIRONMENT": "/runtime/plugin-env"}),
                "--entrypoint",
                _CONTAINER_UV,
                config.task_image,
                "sync",
                "--frozen",
                "--project",
                "/source/integrations/codex/plugins/powercontext",
                "--no-install-project",
            ),
            cwd=paths.runtime,
            timeout=900,
        )
        setup_common = (
            "docker",
            "run",
            "--rm",
            "--cpus",
            config.limits.cpus,
            "--memory",
            config.limits.memory,
            "--pids-limit",
            str(config.limits.pids),
            "--network",
            network,
            "--mount",
            f"type=bind,src={config.source_checkout},dst=/source,readonly",
            "--mount",
            f"type=bind,src={paths.runtime},dst=/runtime",
            "--mount",
            f"type=bind,src={paths.tokensflow_home},dst=/root",
            "--mount",
            _tool_directory_mount(config.codex_binary, "/tools/codex-dir", expected_name="codex"),
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=256m",
            *_docker_env_args(
                {
                    **common_environment,
                    "UV_PROJECT_ENVIRONMENT": "/runtime/plugin-env",
                    "UV_OFFLINE": "1",
                }
            ),
            "--entrypoint",
            _CONTAINER_CODEX,
            config.task_image,
            "plugin",
        )
        self._docker.run(
            (*setup_common, "marketplace", "add", "/source", "--json"),
            cwd=paths.runtime,
            timeout=120,
        )
        for attempt in range(2):
            try:
                self._docker.run(
                    (*setup_common, "add", PLUGIN_ID, "--json"),
                    cwd=paths.runtime,
                    timeout=120,
                )
            except CommandError:
                if attempt == 1:
                    raise
            else:
                break

    def _start_container(
        self,
        config: SutConfig,
        arm: Arm,
        paths: ArmPaths,
        network: str,
        container: str,
        relay_url: str | None,
        tokensflow_environment: Mapping[str, str],
        tokensflow_command_secrets: Sequence[str],
    ) -> None:
        scope = f"eval:{config.run_id}:{arm.value}"
        tokensflow_mounts: tuple[str, ...] = ()
        tokensflow_container_environment: dict[str, str] = {}
        executable_path = (
            "/tools/uv-dir:/tools/codex-dir:/runtime/pc-env/bin:"
            "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        )
        if config.tokensflow_enabled:
            tokensflow_binary, _tokensflow_home = self._validate_tokensflow_inputs(paths)
            tokensflow_mounts = (
                "--mount",
                _tool_directory_mount(tokensflow_binary, "/tools/tokensflow-dir", expected_name="tokensflow"),
                "--mount",
                (
                    f"type=bind,src={paths.runtime.parent / 'evaluation-control' / 'tokensflow-wrapper'},"
                    f"dst={_CONTAINER_TOKENSFLOW_WRAPPER_DIR},readonly"
                ),
            )
            tokensflow_container_environment = {
                "POWERCONTEXT_EVAL_TOKENSFLOW_REAL_BINARY": _CONTAINER_TOKENSFLOW,
            }
            executable_path = (
                f"{_CONTAINER_TOKENSFLOW_WRAPPER_DIR}:"
                "/tools/uv-dir:/tools/codex-dir:/tools/tokensflow-dir:/runtime/pc-env/bin:"
                "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
            )
        command = (
            "docker",
            "run",
            "-d",
            "--init",
            "--name",
            container,
            "--label",
            f"powercontext-eval.run={config.run_id}",
            "--cpus",
            config.limits.cpus,
            "--memory",
            config.limits.memory,
            "--pids-limit",
            str(config.limits.pids),
            "--network",
            network,
            "--mount",
            f"type=bind,src={paths.workspace},dst=/workspace",
            "--mount",
            f"type=bind,src={paths.runtime},dst=/runtime",
            "--mount",
            f"type=bind,src={paths.tokensflow_home},dst=/root",
            "--mount",
            f"type=bind,src={config.source_checkout},dst=/source,readonly",
            "--mount",
            (
                "type=bind,"
                f"src={paths.runtime / 'evaluation-control' / 'record_codex_jsonl.py'},"
                f"dst={_CONTAINER_RECORDER},readonly"
            ),
            "--mount",
            _tool_directory_mount(config.codex_binary, "/tools/codex-dir", expected_name="codex"),
            *tokensflow_mounts,
            "--mount",
            _tool_directory_mount(config.uv_binary, "/tools/uv-dir", expected_name="uv"),
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=1g",
            *_docker_env_args(
                {
                    **runtime_proxy_environment(relay_url, config.extra_no_proxy_hosts),
                    "POWERCONTEXT_HOME": "/runtime/pc-home",
                    "POWERCONTEXT_CODEX_SCOPE_ID": scope,
                    "UV_PROJECT_ENVIRONMENT": "/runtime/plugin-env",
                    "UV_CACHE_DIR": "/runtime/uv-cache",
                    "UV_PYTHON_INSTALL_DIR": _CONTAINER_UV_PYTHON_INSTALL_DIR,
                    "UV_OFFLINE": "1",
                    **tokensflow_container_environment,
                    "PATH": executable_path,
                }
            ),
            *(_docker_inherited_env_args(tokensflow_environment) if config.tokensflow_enabled else ()),
            *_container_env_file_args(config, arm, paths),
            "--entrypoint",
            "/runtime/pc-env/bin/powercontext",
            config.task_image,
            "server",
            "run",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        )
        with docker_pressure.heavy_operation():
            self._docker.run(
                command,
                cwd=paths.runtime,
                timeout=60,
                env=(tokensflow_environment if config.tokensflow_enabled else direct_egress_environment()),
                secrets=tokensflow_command_secrets,
            )

    def _readiness(self, container: str, paths: ArmPaths, store: ArtifactStore) -> None:
        # Probe only the Server readiness contract here.  `powercontext doctor`
        # also shells out to `codex plugin list` with its own 120-second timeout;
        # wrapping that composite command in a 10-second process timeout caused
        # healthy Servers to be misclassified during a full 20-task wave.  Codex
        # and plugin invariants have dedicated gates immediately before and after
        # this one, so repeating them here adds latency without adding evidence.
        command = (
            "docker",
            "exec",
            container,
            "/runtime/pc-env/bin/python",
            "-c",
            _SERVER_READINESS_PROBE_SCRIPT,
        )
        deadline = time.monotonic() + _READINESS_BUDGET_SECONDS
        attempts = 0
        timed_out_attempts = 0
        last_reason = ReadinessFailureReason.PROBE_FAILED
        while time.monotonic() < deadline:
            attempts += 1
            try:
                result = self._docker.run(
                    command,
                    cwd=paths.runtime,
                    timeout=_READINESS_ATTEMPT_TIMEOUT_SECONDS,
                    check=False,
                )
            except CommandTimedOut:
                timed_out_attempts += 1
                last_reason = ReadinessFailureReason.COMMAND_TIMED_OUT
            else:
                if result.returncode == 0:
                    store.write_json(
                        "powercontext/readiness.json",
                        {
                            "attempts": attempts,
                            "budget_seconds": _READINESS_BUDGET_SECONDS,
                            "last_outcome": "ready",
                            "probe_timeout_seconds": _READINESS_ATTEMPT_TIMEOUT_SECONDS,
                            "server_ready": True,
                            "timed_out_attempts": timed_out_attempts,
                        },
                    )
                    return
                last_reason = {
                    10: ReadinessFailureReason.SERVER_NOT_READY,
                    11: ReadinessFailureReason.MALFORMED_RESPONSE,
                }.get(result.returncode, ReadinessFailureReason.PROBE_FAILED)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(_READINESS_RETRY_SECONDS, remaining))
        store.write_json(
            "powercontext/readiness.json",
            {
                "attempts": attempts,
                "budget_seconds": _READINESS_BUDGET_SECONDS,
                "last_outcome": last_reason.value,
                "probe_timeout_seconds": _READINESS_ATTEMPT_TIMEOUT_SECONDS,
                "server_ready": False,
                "timed_out_attempts": timed_out_attempts,
            },
        )
        raise ReadinessFailure(last_reason)

    def _verify_codex_version(self, container: str, paths: ArmPaths, store: ArtifactStore) -> None:
        with docker_pressure.heavy_operation():
            result = self._docker.run(
                ("docker", "exec", container, _CONTAINER_CODEX, "--version"),
                cwd=paths.runtime,
                timeout=30,
            )
        match = re.fullmatch(r"codex-cli ([0-9]+\.[0-9]+\.[0-9]+)\n?", result.stdout)
        if match is None or match.group(1) != EXPECTED_CODEX_VERSION:
            raise InvalidTreatment("Codex CLI version does not match the pinned experiment")
        store.write_json(
            "codex/provenance.json",
            {"actual_version": match.group(1), "expected_version": EXPECTED_CODEX_VERSION},
        )

    def _plugin_list(self, container: str, paths: ArmPaths) -> tuple[str, str]:
        deadline = time.monotonic() + _PLUGIN_LIST_BUDGET_SECONDS
        while True:
            try:
                result = self._docker.run(
                    ("docker", "exec", container, _CONTAINER_CODEX, "plugin", "list", "--json"),
                    cwd=paths.runtime,
                    timeout=_PLUGIN_LIST_ATTEMPT_TIMEOUT_SECONDS,
                )
            except CommandTimedOut:
                if time.monotonic() >= deadline:
                    raise PluginInspectionFailure(PluginInspectionFailureReason.TIMED_OUT) from None
                time.sleep(_PLUGIN_LIST_RETRY_SECONDS)
                continue
            try:
                value = json.loads(result.stdout)
                if not isinstance(value, dict) or value.get("available") != []:
                    raise TypeError
                plugins = value["installed"]
                if not isinstance(plugins, list) or len(plugins) != 1 or not isinstance(plugins[0], dict):
                    raise TypeError
                plugin = plugins[0]
                plugin_id = plugin["pluginId"]
                version = plugin["version"]
                if (
                    not isinstance(plugin_id, str)
                    or not plugin_id
                    or not isinstance(version, str)
                    or not version
                    or plugin.get("installed") is not True
                ):
                    raise TypeError
            except (json.JSONDecodeError, KeyError, TypeError):
                if time.monotonic() >= deadline:
                    raise PluginInspectionFailure(PluginInspectionFailureReason.INVALID_PLUGIN_SET) from None
                time.sleep(_PLUGIN_LIST_RETRY_SECONDS)
                continue
            return plugin_id, version

    def _evidence(
        self,
        config: SutConfig,
        arm: Arm,
        container: str,
        paths: ArmPaths,
        plugin: tuple[str, str],
    ) -> TreatmentEvidence:
        scope = f"eval:{config.run_id}:{arm.value}"
        query = (
            "import json,sqlite3,sys;"
            "db=sqlite3.connect('/runtime/pc-home/powercontext.db');"
            "count=db.execute('SELECT COUNT(*) FROM pc_sources WHERE scope_id = ?', (sys.argv[1],)).fetchone()[0];"
            "print(json.dumps({'prompt_sources':count}))"
        )
        result = self._docker.run(
            (
                "docker",
                "exec",
                container,
                "/runtime/pc-env/bin/python",
                "-c",
                query,
                scope,
                "evidence",
            ),
            cwd=paths.runtime,
            timeout=60,
        )
        try:
            raw = json.loads(result.stdout)
            prompt_sources = raw["prompt_sources"]
            if isinstance(prompt_sources, bool) or not isinstance(prompt_sources, int):
                raise TypeError
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise InvalidTreatment("PowerContext SQLite evidence is malformed") from error
        logs = self._docker.run(
            ("docker", "logs", container),
            cwd=paths.runtime,
            timeout=30,
            check=False,
        )
        mcp_requests = sum("/mcp" in line for line in (logs.stdout + logs.stderr).splitlines())
        return TreatmentEvidence(
            plugin_installed=True,
            plugin_id=plugin[0],
            plugin_version=plugin[1],
            plugin_checkout_sha=config.plugin_checkout_sha,
            server_ready=True,
            prompt_sources=prompt_sources,
            mcp_requests=mcp_requests,
            scope_id=scope,
        )


def _validated_gateway(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise UnsafeSutConfiguration("Docker gateway is invalid") from error
    if address.is_loopback or address.is_unspecified or address.is_multicast or not address.is_private:
        raise UnsafeSutConfiguration("Docker gateway must be a private bridge address")
    return str(address)


def _validated_docker_network_pool(value: str) -> ipaddress.IPv4Network:
    try:
        pool = ipaddress.ip_network(value, strict=True)
    except ValueError as error:
        raise UnsafeSutConfiguration("Docker network pool is invalid") from error
    if not isinstance(pool, ipaddress.IPv4Network) or not pool.is_private or pool.prefixlen > 23:
        raise UnsafeSutConfiguration("Docker network pool must be a private IPv4 network with at least 32 /28 subnets")
    return pool


def _validated_no_proxy_hosts(values: tuple[str, ...]) -> tuple[str, ...]:
    safe_host = re.compile(r"(?:[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|\[[0-9A-Fa-f:]+\])")
    if len(values) != len(set(values)) or any(safe_host.fullmatch(value) is None for value in values):
        raise UnsafeSutConfiguration("Additional no-proxy hosts are invalid")
    return values


def _docker_network_subnet_candidates(run_id: str, network_pool: str = DEFAULT_DOCKER_NETWORK_POOL) -> tuple[str, ...]:
    """Return a deterministic, bounded probe sequence from the evaluation-only pool."""

    pool = _validated_docker_network_pool(network_pool)
    subnet_size = 1 << (32 - _DOCKER_NETWORK_PREFIX_LENGTH)
    subnet_count = pool.num_addresses // subnet_size
    start = int.from_bytes(hashlib.sha256(run_id.encode("ascii")).digest()[:8], "big") % subnet_count
    return tuple(
        str(
            ipaddress.ip_network(
                (
                    int(pool.network_address) + ((start + offset) % subnet_count) * subnet_size,
                    _DOCKER_NETWORK_PREFIX_LENGTH,
                ),
            )
        )
        for offset in range(min(_DOCKER_NETWORK_SUBNET_ATTEMPTS, subnet_count))
    )


def _is_docker_network_subnet_collision(error: CommandError) -> bool:
    output = f"{error.result.stdout}\n{error.result.stderr}".casefold()
    return any(marker in output for marker in _DOCKER_NETWORK_SUBNET_COLLISION_MARKERS)


def _reserve_port(address: str) -> int:
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as listener:
        listener.bind((address, 0))
        port = listener.getsockname()[1]
    return int(port)


def _docker_env_args(environment: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(part for key, value in environment.items() for part in ("-e", f"{key}={value}"))


def _docker_inherited_env_args(environment: Mapping[str, str]) -> tuple[str, ...]:
    """Ask Docker to inherit selected values without placing them in argv."""

    return tuple(part for key in environment for part in ("--env", key))


def _container_env_file_args(
    config: SutConfig,
    arm: Arm,
    paths: ArmPaths,
) -> tuple[str, ...]:
    """Write user-supplied env to a file and return the --env-file docker flag.

    Only the ON arm receives user env; the OFF arm stays pristine. Docker reads
    --env-file line by line without shell expansion, avoiding escaping issues.
    """

    if arm is not Arm.ON or not config.container_env:
        return ()
    reserved = {"NO_PROXY", "no_proxy", "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"}
    safe_env = {k: v for k, v in config.container_env.items() if k not in reserved}
    if not safe_env:
        return ()
    env_file = paths.runtime / "container.env"
    env_file.write_text(
        "".join(f"{key}={value}\n" for key, value in safe_env.items()),
        encoding="utf-8",
    )
    return ("--env-file", str(env_file))


def _directory_entry_matches(parent_fd: int, name: str, opened_fd: int) -> bool:
    """Confirm a directory name still identifies the descriptor-opened object."""

    named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    opened = os.fstat(opened_fd)
    return stat.S_ISDIR(named.st_mode) and (named.st_dev, named.st_ino) == (opened.st_dev, opened.st_ino)


def _read_all(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    while chunk := os.read(descriptor, 64 * 1024):
        chunks.append(chunk)
    return b"".join(chunks)


def _tool_directory_mount(
    binary: Path,
    destination: str,
    *,
    expected_name: str,
    require_executable: bool = False,
) -> str:
    raw = os.fspath(binary)
    if not binary.is_absolute() or "\0" in raw or ".." in binary.parts:
        raise UnsafeSutConfiguration("Tool binary path is unsafe")
    try:
        resolved = binary.resolve(strict=True)
        metadata = resolved.stat()
    except OSError as error:
        raise UnsafeSutConfiguration("Tool binary path is unsafe") from error
    if resolved.name != expected_name or not stat.S_ISREG(metadata.st_mode):
        raise UnsafeSutConfiguration("Tool binary has an unexpected name or type")
    if require_executable and not os.access(resolved, os.X_OK):
        raise UnsafeSutConfiguration("Tool binary is not executable")
    return f"type=bind,src={resolved.parent},dst={destination},readonly"


def _reject_retained_secrets(data: bytes, variants: tuple[str, ...]) -> None:
    if any(value.encode("utf-8") in data for value in variants):
        raise CodexInfrastructureError("Retained artifact contained an unredacted auth secret")


def run_codex_contract_smoke(
    *,
    run_root: str,
    task_image: str,
    codex_bin: str,
    tokensflow_bin: str,
    tokensflow_user_home: str,
    tokensflow_egress_network: str,
    uv_bin: str,
    powercontext_source: str,
    powercontext_sha: str,
    auth_json: str,
    proxy_url: str,
    prompt: str = "Reply with exactly OK.",
    sut_factory: Callable[[ProcessRunner], Any] = DockerSut,
) -> dict[str, object]:
    """Execute the real disposable OFF/ON contract through an injectable Docker adapter."""

    root = Path(run_root).absolute()
    try:
        root.mkdir(parents=True, exist_ok=False, mode=0o700)
    except FileExistsError as error:
        raise UnsafeSutConfiguration("Contract smoke root must be fresh") from error
    source = Path(powercontext_source).absolute()
    auth = Path(auth_json).absolute()
    variants = auth_secret_variants(auth)
    config = SutConfig(
        run_id="contract-smoke",
        task_image=task_image,
        codex_binary=Path(codex_bin).absolute(),
        uv_binary=Path(uv_bin).absolute(),
        source_checkout=source,
        plugin_checkout_sha=powercontext_sha,
        proxy=ProxyRelayConfig(proxy_url),
        tokensflow_enabled=True,
        tokensflow_binary=Path(tokensflow_bin).absolute(),
        tokensflow_egress_network=tokensflow_egress_network,
    )
    paths: dict[Arm, ArmPaths] = {}
    stores: dict[Arm, ArtifactStore] = {}
    for arm in (Arm.OFF, Arm.ON):
        arm_root = root / arm.value
        arm_root.mkdir(mode=0o700)
        runtime = arm_root / "ephemeral/runtime"
        try:
            tokensflow = snapshot_tokensflow_home(
                Path(tokensflow_user_home).absolute(),
                runtime / "root-home",
            )
        except UnsafeTokensFlowConfiguration:
            raise TokensFlowInfrastructureError("TokensFlow profile snapshot failed") from None
        paths[arm] = ArmPaths(
            source=source,
            auth_source=auth,
            workspace=arm_root / "ephemeral/workspace",
            runtime=runtime,
            codex_home=runtime / "root-home/.codex",
            pc_home=runtime / "pc-home",
            result_root=arm_root / "results",
            tokensflow_home=tokensflow.user_home,
        )
        stores[arm] = ArtifactStore(
            paths[arm].result_root,
            forbidden_values=variants + tokensflow_secret_variants(tokensflow.credentials),
        )
    outcomes = sut_factory(ProcessRunner()).run_pair(
        config,
        paths=paths,
        prompts={Arm.OFF: prompt.encode("utf-8"), Arm.ON: prompt.encode("utf-8")},
        stores=stores,
    )
    off_tokensflow = outcomes[Arm.OFF].tokensflow
    on_tokensflow = outcomes[Arm.ON].tokensflow
    if off_tokensflow is None or on_tokensflow is None:
        raise TokensFlowInfrastructureError("TokensFlow contract smoke did not produce evidence")
    return {
        "status": "passed",
        "off_prompt_sources": outcomes[Arm.OFF].evidence.prompt_sources,
        "on_prompt_sources": outcomes[Arm.ON].evidence.prompt_sources,
        "tokensflow": {
            Arm.OFF.value: off_tokensflow.as_dict(),
            Arm.ON.value: on_tokensflow.as_dict(),
        },
        "run_root": os.fspath(root),
    }
