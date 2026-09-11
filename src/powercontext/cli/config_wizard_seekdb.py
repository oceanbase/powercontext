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

"""Incremental background installation for the guided seekdb storage choice."""

from __future__ import annotations

import importlib
import importlib.metadata
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

ALIYUN_SIMPLE_INDEX = "https://mirrors.aliyun.com/pypi/simple/"
_CHINA_TIMEZONES = {
    "PRC",
    "Asia/Chongqing",
    "Asia/Harbin",
    "Asia/Hong_Kong",
    "Asia/Kashgar",
    "Asia/Macau",
    "Asia/Shanghai",
    "Asia/Urumqi",
}


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Bounded subprocess result used by the installer and deterministic tests."""

    returncode: int
    stderr: str


@dataclass(frozen=True, slots=True)
class SeekDBInstallPlan:
    """Exact incremental installation target derived from the running release."""

    uv: str
    python: str
    requirements: tuple[str, ...]
    explicit_index: bool = False
    prefer_aliyun: bool = False

    def command(self, *, dry_run: bool = False, index_url: str | None = None) -> tuple[str, ...]:
        command = [self.uv, "pip", "install", "--python", self.python, "--strict", "--no-progress"]
        if dry_run:
            command.append("--dry-run")
        if index_url is not None:
            command.extend(("--default-index", index_url))
        command.extend(self.requirements)
        return tuple(command)


@dataclass(frozen=True, slots=True)
class SeekDBDependency:
    """Current dependency state before the wizard starts background work."""

    status: Literal["ready", "installable", "unavailable"]
    version: str = ""
    reason: str = ""
    plan: SeekDBInstallPlan | None = None


@dataclass(frozen=True, slots=True)
class SeekDBInstallResult:
    """Sanitized final result safe to show in terminal output."""

    status: Literal["ready", "failed", "unsupported", "cancelled"]
    version: str = ""
    reason: str = ""
    manual_command: str = ""


CommandRunner = Callable[[tuple[str, ...]], CommandResult]
VersionReader = Callable[[], str | None]
PhaseWriter = Callable[[str], None]
CancelReader = Callable[[], bool]


def declared_seekdb_requirements(declared: Sequence[str], *, platform: str = sys.platform) -> tuple[str, ...]:
    """Return current-release seekdb requirements applicable to this platform."""

    environment: dict[str, str] = {**default_environment(), "extra": "seekdb", "sys_platform": platform}
    selected: list[str] = []
    for raw in declared:
        try:
            requirement = Requirement(raw)
        except InvalidRequirement:
            continue
        if requirement.marker is not None and not requirement.marker.evaluate(environment):
            continue
        if canonicalize_name(requirement.name) == "powercontext":
            continue
        rendered = requirement.name
        if requirement.extras:
            rendered += "[" + ",".join(sorted(requirement.extras)) + "]"
        if requirement.url is not None:
            rendered += f" @ {requirement.url}"
        else:
            rendered += str(requirement.specifier)
        if rendered not in selected:
            selected.append(rendered)
    return tuple(selected)


def is_china_timezone(value: str) -> bool:
    """Return whether one explicit IANA timezone name represents China."""

    normalized = value.strip().lstrip(":")
    return normalized in _CHINA_TIMEZONES or any(normalized.endswith("/" + zone) for zone in _CHINA_TIMEZONES)


def _system_uses_china_timezone() -> bool:
    if "TZ" in os.environ:
        return is_china_timezone(os.environ["TZ"])
    candidates = []
    for timezone_file in (Path("/etc/timezone"), Path("/var/db/timezone/zoneinfo")):
        with suppress(OSError):
            candidates.append(timezone_file.read_text(encoding="utf-8").strip())
    with suppress(OSError):
        candidates.append(str(Path("/etc/localtime").resolve()))
    return any(is_china_timezone(candidate) for candidate in candidates)


def _installation_indexes(plan: SeekDBInstallPlan) -> tuple[str | None, ...]:
    if plan.explicit_index:
        return (None,)
    if plan.prefer_aliyun:
        return (ALIYUN_SIMPLE_INDEX,)
    return (None, ALIYUN_SIMPLE_INDEX)


def inspect_seekdb_dependency() -> SeekDBDependency:
    """Inspect the running distribution without importing or opening seekdb."""

    if sys.platform not in {"linux", "darwin"}:
        return SeekDBDependency("unavailable", reason="embedded seekdb supports Linux and macOS only")
    if version := _validated_version():
        return SeekDBDependency("ready", version=version)
    uv = shutil.which("uv")
    if uv is None:
        return SeekDBDependency("unavailable", reason="uv is required to install the seekdb dependency")
    try:
        declared = importlib.metadata.requires("powercontext") or ()
    except importlib.metadata.PackageNotFoundError:
        return SeekDBDependency("unavailable", reason="PowerContext release metadata is unavailable")
    requirements = declared_seekdb_requirements(declared)
    if not any(canonicalize_name(Requirement(item).name) == "pylibseekdb" for item in requirements):
        return SeekDBDependency("unavailable", reason="this PowerContext release does not declare the seekdb extra")
    explicit_index = bool(os.environ.get("UV_DEFAULT_INDEX") or os.environ.get("UV_INDEX_URL"))
    return SeekDBDependency(
        "installable",
        plan=SeekDBInstallPlan(
            uv,
            sys.executable,
            requirements,
            explicit_index=explicit_index,
            prefer_aliyun=not explicit_index and _system_uses_china_timezone(),
        ),
    )


def run_seekdb_install(
    plan: SeekDBInstallPlan,
    *,
    runner: CommandRunner | None = None,
    version_reader: VersionReader | None = None,
    phase_writer: PhaseWriter | None = None,
    cancelled: CancelReader | None = None,
) -> SeekDBInstallResult:
    """Resolve, install, and validate, falling back to Aliyun when appropriate."""

    run = runner or _run_command
    read_version = version_reader or _validated_version
    set_phase = phase_writer or (lambda _phase: None)
    is_cancelled = cancelled or (lambda: False)
    indexes = _installation_indexes(plan)
    failures: list[str] = []
    for index in indexes:
        if is_cancelled():
            return SeekDBInstallResult("cancelled")
        set_phase("resolving-mirror" if index else "resolving")
        resolution = run(plan.command(dry_run=True, index_url=index))
        if resolution.returncode != 0:
            failures.append(resolution.stderr)
            continue
        if is_cancelled():
            return SeekDBInstallResult("cancelled")
        set_phase("installing-mirror" if index else "installing")
        installation = run(plan.command(index_url=index))
        if installation.returncode != 0:
            failures.append(installation.stderr)
            continue
        if is_cancelled():
            return SeekDBInstallResult("cancelled")
        set_phase("validating")
        if version := read_version():
            return SeekDBInstallResult("ready", version=version)
    if failures and all(_is_incompatible_wheel_failure(stderr) for stderr in failures):
        return SeekDBInstallResult(
            "unsupported",
            reason="no compatible seekdb wheel is available for this Python and platform",
        )
    return SeekDBInstallResult(
        "failed",
        reason="dependency resolution or installation failed",
        manual_command=shlex.join(plan.command(index_url=None if plan.explicit_index else ALIYUN_SIMPLE_INDEX)),
    )


def _is_incompatible_wheel_failure(stderr: str) -> bool:
    normalized = " ".join(stderr.lower().split())
    wheel_markers = ("no wheels", "does not have a wheel", "no compatible wheel", "matching python abi tag")
    return "no solution found" in normalized and any(marker in normalized for marker in wheel_markers)


class SeekDBInstallTask:
    """One non-blocking installation whose terminal wait occurs at wizard completion."""

    def __init__(
        self,
        plan: SeekDBInstallPlan,
        *,
        runner: CommandRunner | None = None,
        version_reader: VersionReader | None = None,
    ) -> None:
        self._plan = plan
        self._executor = _CommandExecutor()
        self._runner = runner or self._executor.run
        self._version_reader = version_reader
        self._phase = "starting"
        self._result: SeekDBInstallResult | None = None
        self._lock = threading.Lock()
        self._finished = threading.Event()
        self._cancelled = threading.Event()
        self._started_at = time.monotonic()
        self._thread = threading.Thread(target=self._work, name="powercontext-seekdb-install", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _work(self) -> None:
        try:
            result = run_seekdb_install(
                self._plan,
                runner=self._runner,
                version_reader=self._version_reader,
                phase_writer=self._set_phase,
                cancelled=self._cancelled.is_set,
            )
        except Exception:
            result = SeekDBInstallResult(
                "failed",
                reason="dependency installer failed unexpectedly",
                manual_command=shlex.join(
                    self._plan.command(index_url=None if self._plan.explicit_index else ALIYUN_SIMPLE_INDEX)
                ),
            )
        with self._lock:
            self._result = result
        self._finished.set()

    def _set_phase(self, phase: str) -> None:
        with self._lock:
            self._phase = phase

    def done(self) -> bool:
        return self._finished.is_set()

    def cancel(self) -> None:
        self._cancelled.set()
        if self._executor is not None:
            self._executor.cancel()

    def phase(self) -> str:
        with self._lock:
            return self._phase

    def elapsed_seconds(self) -> float:
        return max(0.0, time.monotonic() - self._started_at)

    def wait(self, timeout: float | None = None) -> SeekDBInstallResult:
        if not self._finished.wait(timeout):
            raise TimeoutError
        with self._lock:
            result = self._result
        if result is None:
            raise RuntimeError
        return result


def start_seekdb_install(
    plan: SeekDBInstallPlan,
    *,
    runner: CommandRunner | None = None,
    version_reader: VersionReader | None = None,
) -> SeekDBInstallTask:
    """Start one background installation and return immediately."""

    task = SeekDBInstallTask(plan, runner=runner, version_reader=version_reader)
    task.start()
    return task


def _run_command(command: tuple[str, ...]) -> CommandResult:
    try:
        result = subprocess.run(  # noqa: S603 - argument tuple is built without a shell from release metadata
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=1_800,
        )
    except (OSError, subprocess.TimeoutExpired):
        return CommandResult(2, "")
    return CommandResult(result.returncode, result.stderr[-2_000:])


class _CommandExecutor:
    """Run one uv command at a time and allow wizard cancellation to terminate it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._cancelled = False

    def run(self, command: tuple[str, ...]) -> CommandResult:
        with self._lock:
            if self._cancelled:
                return CommandResult(130, "")
            try:
                process = subprocess.Popen(  # noqa: S603 - fixed uv arguments, no shell
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            except OSError:
                return CommandResult(2, "")
            self._process = process
        try:
            _, stderr = process.communicate(timeout=1_800)
        except subprocess.TimeoutExpired:
            process.terminate()
            _, stderr = process.communicate()
        finally:
            with self._lock:
                self._process = None
        return CommandResult(process.returncode or 0, stderr[-2_000:])

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            process = self._process
        if process is not None and process.poll() is None:
            process.terminate()


def _validated_version() -> str | None:
    try:
        importlib.import_module("pylibseekdb")
        return importlib.metadata.version("pylibseekdb")
    except (ImportError, importlib.metadata.PackageNotFoundError, OSError):
        return None
