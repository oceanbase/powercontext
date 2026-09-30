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

"""Transactional orchestration for one native personal Server registration."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import sys
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager, nullcontext, suppress
from dataclasses import dataclass, replace
from importlib.metadata import version
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from powercontext.cli.env_file import environment_context
from powercontext.paths import POWERCONTEXT_HOME_ENV, powercontext_data_dir
from powercontext.server.configuration import ServerConfigurationError, server_settings_context
from powercontext.service.adapters import NativeServiceAdapter, native_service_adapter
from powercontext.service.adapters.base import atomic_write, definition_state, service_python_executable
from powercontext.service.environment import ProtectedEnvironmentFileError, load_protected_environment_file
from powercontext.service.model import (
    DEFINITION_VERSION,
    OWNERSHIP_MARKER,
    DefinitionState,
    LivenessState,
    ManagerOwnershipState,
    ManagerRegistration,
    ManagerState,
    NativeRegistration,
    ProbeResult,
    ProbeState,
    RegistrationState,
    ServiceDefinition,
    ServiceError,
    ServiceStatus,
    SupportState,
)
from powercontext.service.probe import probe_server
from powercontext.transport import is_loopback_host

_PERSISTED_ENVIRONMENT_PREFIX = "POWERCONTEXT_SERVER_"
# A wall-clock budget cannot bound a first start on every supported machine: the
# interpreter has to compile its bytecode cache before the Server can bind the port, and
# that cost depends on the hardware. Past this budget the native job's own lifecycle
# decides instead, so a job that is still running keeps one more grace period.
_START_TIMEOUT_SECONDS = 60.0
_START_GRACE_SECONDS = 120.0
_MANAGER_RECHECK_SECONDS = 5.0


@dataclass(frozen=True)
class ServiceMaintenanceSummary:
    """The exact local registration and new executable accepted in a migration plan."""

    identifier: str
    artifact_path: str
    definition: ServiceDefinition
    target_definition: ServiceDefinition
    originally_running: bool
    registration_checksum: str

    def as_dict(self) -> dict[str, object]:
        return {**self._payload(), "fingerprint": self.fingerprint}

    def _payload(self) -> dict[str, object]:
        return {
            "identifier": self.identifier,
            "artifact_path": self.artifact_path,
            "definition": self.definition.as_dict(),
            "target_definition": self.target_definition.as_dict(),
            "originally_running": self.originally_running,
            "registration_checksum": self.registration_checksum,
            "scope": "current-user local registration only; other writers require external coordination",
        }

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(self._payload(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class ServiceMaintenanceSession:
    """A stopped service, with an explicit database-ready completion boundary."""

    def __init__(self, controller: ServiceController, summary: ServiceMaintenanceSummary) -> None:
        self._controller = controller
        self.summary = summary
        self.completed = False
        self._closed = False

    def complete(self) -> ServiceStatus:
        """Switch to the confirmed new executable only after database verification."""

        if self._closed or self.completed:
            raise ServiceError("service maintenance completion requires an active, uncompleted session")  # noqa: TRY003
        status = self._controller._complete_maintenance(self.summary)
        self.completed = True
        return status


class ServiceController:
    def __init__(
        self,
        adapter: NativeServiceAdapter | None = None,
        *,
        probe: Callable[[str], ProbeResult] = probe_server,
        readiness_probe: Callable[[str], ProbeResult] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._adapter = native_service_adapter() if adapter is None else adapter
        self._probe = probe
        if readiness_probe is None:
            from powercontext.service.probe import probe_readiness

            readiness_probe = probe_readiness
        self._readiness_probe = readiness_probe
        self._sleep = sleep

    def install(self, *, env_file: Path | None = None, start_on_login: bool = True) -> ServiceStatus:
        if not start_on_login and sys.platform != "win32":
            raise ServiceError(  # noqa: TRY003
                "disabling login auto-start is currently supported only on Windows",
                exit_code=2,
            )
        support, detail = self._adapter.support()
        if support is SupportState.UNSUPPORTED:
            raise ServiceError(detail)
        definition = self._build_definition(env_file, start_on_login=start_on_login)
        initial_probe = self._probe(definition.endpoint)
        if initial_probe.state is ProbeState.CONFLICT:
            raise ServiceError(  # noqa: TRY003
                f"refusing to install over another listener: {initial_probe.detail}"
            )

        with _service_lock(self._adapter.lock_path):
            if self._maintenance_record() is not None:
                raise ServiceError(  # noqa: TRY003
                    "the personal service is stopped for maintenance; finish migration or use service start first"
                )
            registration = self._adapter.inspect()
            self._require_mutable_registration(registration)
            loaded = self._adapter.loaded_registration()
            self._require_mutable_manager_registration(loaded)
            # Native settings such as launchd's scheduling policy are not
            # represented in the shared metadata, but still require a reload.
            changed = registration.definition != definition or registration.content != self._adapter.render(definition)
            loaded_changed = loaded.state is ManagerOwnershipState.OWNED and loaded.definition != definition
            manager_before = (
                self._adapter.manager_state() if loaded.state is ManagerOwnershipState.OWNED else ManagerState.INACTIVE
            )
            if changed:
                self._commit_definition(definition, registration.content)
            else:
                self._adapter.enable()

            should_restart = manager_before is ManagerState.ACTIVE and (
                changed or loaded_changed or initial_probe.state is ProbeState.UNREACHABLE
            )
            if loaded_changed or should_restart or initial_probe.state is not ProbeState.LIVE:
                try:
                    self._adapter.start(reload_definition=changed or loaded_changed or should_restart)
                except (OSError, ServiceError) as error:
                    raise self._post_commit_error(  # noqa: TRY003
                        f"the personal service was registered but the native manager could not start it: {error}",
                        exit_code=error.exit_code if isinstance(error, ServiceError) else 1,
                    ) from error
                started = time.monotonic()
                final_probe = self._wait_until_live(definition.endpoint)
                if final_probe.state is not ProbeState.LIVE:
                    elapsed = time.monotonic() - started
                    raise self._post_commit_error(  # noqa: TRY003
                        f"the personal service was registered but did not become live within {elapsed:.0f}s:"
                        f" {final_probe.detail}; "
                        f"inspect {self._adapter.log_location(definition) or 'the native service logs'}"
                    )
        return self.status()

    @property
    def maintenance_path(self) -> Path:
        return self._adapter.lock_path.with_name(f"{self._adapter.lock_path.name}.maintenance.json")

    def start(self) -> ServiceStatus:
        """Start the registered executable without reinstalling or changing configuration."""

        with _service_lock(self._adapter.lock_path):
            registration, running = self._owned_registration()
            record = self._maintenance_record()
            if record is not None and record.get("phase") not in {"manual_stopped", "database_ready", "start_failed"}:
                raise ServiceError(  # noqa: TRY003
                    "the database migration has not been verified; complete it before starting the service"
                )
            if running and record is None:
                definition = registration.definition
                if definition is None or self._readiness_probe(definition.endpoint).state is not ProbeState.LIVE:
                    raise ServiceError("the running service is not ready; inspect its diagnostics")  # noqa: TRY003
                return self.status()
            if record is None:
                # A failed start must also leave a durable activation guard;
                # systemd's condition alone is ineffective without this file.
                self._write_maintenance_record({"phase": "manual_stopped"})
            self._start_registered(registration.definition)
            self.maintenance_path.unlink(missing_ok=True)
        return self.status()

    def stop(self) -> ServiceStatus:
        """Stop the owned service and persistently suppress native automatic activation."""

        with _service_lock(self._adapter.lock_path):
            registration, _running = self._owned_registration()
            record = self._maintenance_record()
            if record is None:
                self._write_maintenance_record({"phase": "manual_stopped"})
            self._adapter.suspend(self.maintenance_path)
            self._adapter.stop()
            self._require_stopped(registration.definition)
        return self.status()

    def restart(self) -> ServiceStatus:
        """Stop then start the registered executable; migration is a separate operation."""

        self.stop()
        return self.start()

    def maintenance_summary(self, *, env_file: Path | None = None) -> ServiceMaintenanceSummary:
        registration, running = self._owned_registration()
        definition = registration.definition
        if definition is None:
            raise ServiceError("the installed service has no definition")  # noqa: TRY003
        try:
            if env_file is not None:
                expected = load_protected_environment_file(env_file).identity
                if definition.env_file != expected:
                    raise ServiceError(  # noqa: TRY003
                        "the migration environment file does not match the registered service configuration",
                        exit_code=2,
                    )
            elif definition.env_file is not None:
                load_protected_environment_file(Path(definition.env_file.path), expected=definition.env_file)
        except ProtectedEnvironmentFileError as error:
            raise ServiceError(f"the registered environment file is not usable: {error}", exit_code=2) from error  # noqa: TRY003
        record = self._maintenance_record()
        if record is not None and record.get("phase") not in {"manual_stopped", "database_ready", "start_failed"}:
            previous = record.get("summary")
            if not isinstance(previous, dict) or previous.get("definition") != definition.as_dict():
                raise ServiceError("the interrupted service maintenance record does not match the registration")  # noqa: TRY003
            running = previous.get("originally_running")
            if not isinstance(running, bool):
                raise ServiceError("the interrupted service maintenance record is invalid")  # noqa: TRY003
        target = replace(
            definition,
            definition_version=DEFINITION_VERSION,
            package_version=version("powercontext"),
            python_executable=service_python_executable(),
        )
        return ServiceMaintenanceSummary(
            self._adapter.identifier,
            str(self._adapter.artifact_path),
            definition,
            target,
            running,
            hashlib.sha256(registration.content or b"").hexdigest(),
        )

    @contextmanager
    def maintenance(
        self,
        *,
        expected_fingerprint: str,
        env_file: Path | None = None,
    ) -> Generator[ServiceMaintenanceSession, None, None]:
        """Keep the local service stopped across backup/migration; failures retain the guard."""

        with _service_lock(self._adapter.lock_path):
            summary = self.maintenance_summary(env_file=env_file)
            if summary.fingerprint != expected_fingerprint:
                raise ServiceError("the service configuration or running state changed; review a new plan")  # noqa: TRY003
            self._write_maintenance_record({"phase": "stopping", "summary": summary.as_dict()})
            self._adapter.suspend(self.maintenance_path)
            self._adapter.stop()
            self._require_stopped(summary.definition)
            self._write_maintenance_record({"phase": "stopped", "summary": summary.as_dict()})
            session = ServiceMaintenanceSession(self, summary)
            try:
                yield session
            finally:
                session._closed = True

    def _complete_maintenance(self, summary: ServiceMaintenanceSummary) -> ServiceStatus:
        registration, running = self._owned_registration()
        if (
            running
            or registration.definition != summary.definition
            or hashlib.sha256(registration.content or b"").hexdigest() != summary.registration_checksum
        ):
            raise ServiceError("the stopped service registration changed during migration")  # noqa: TRY003
        self._adapter.write(self._adapter.render(summary.target_definition))
        self._adapter.update_suspended()
        self._write_maintenance_record({"phase": "database_ready", "summary": summary.as_dict()})
        if summary.originally_running:
            try:
                self._start_registered(summary.target_definition)
            except (OSError, ServiceError):
                self._write_maintenance_record({"phase": "start_failed", "summary": summary.as_dict()})
                raise
            self.maintenance_path.unlink(missing_ok=True)
        return self.status()

    def _owned_registration(self) -> tuple[NativeRegistration, bool]:
        support, detail = self._adapter.support()
        if support is SupportState.UNSUPPORTED:
            raise ServiceError(detail)
        registration = self._adapter.inspect()
        self._require_mutable_registration(registration)
        if registration.state is not RegistrationState.INSTALLED or registration.definition is None:
            raise ServiceError("no owned personal service is installed; use external service management")  # noqa: TRY003
        try:
            endpoint = urlsplit(registration.definition.endpoint)
            if endpoint.scheme != "http" or endpoint.port is None or not is_loopback_host(endpoint.hostname or ""):
                raise ValueError("a local HTTP endpoint is required")  # noqa: TRY003, TRY301
        except ValueError as error:
            raise ServiceError("the service registration has an invalid local endpoint", exit_code=2) from error  # noqa: TRY003
        loaded = self._adapter.loaded_registration()
        self._require_mutable_manager_registration(loaded)
        if loaded.state is ManagerOwnershipState.OWNED and loaded.definition != registration.definition:
            raise ServiceError("loaded and installed service definitions differ; resolve them before maintenance")  # noqa: TRY003
        manager = (
            self._adapter.manager_state() if loaded.state is ManagerOwnershipState.OWNED else ManagerState.INACTIVE
        )
        if manager is ManagerState.UNKNOWN:
            raise ServiceError("cannot determine the personal service running state")  # noqa: TRY003
        probe = self._probe(registration.definition.endpoint)
        if probe.state is ProbeState.CONFLICT or (
            manager is not ManagerState.ACTIVE and probe.state is ProbeState.LIVE
        ):
            raise ServiceError("another listener may own the registered endpoint; external maintenance is required")  # noqa: TRY003
        return registration, manager is ManagerState.ACTIVE

    def _require_stopped(self, definition: ServiceDefinition | None) -> None:
        if definition is None:
            raise ServiceError("the installed service has no definition")  # noqa: TRY003
        deadline = time.monotonic() + 30.0
        while True:
            state = self._adapter.manager_state()
            probe = self._probe(definition.endpoint)
            if state in {ManagerState.INACTIVE, ManagerState.FAILED} and probe.state is ProbeState.UNREACHABLE:
                return
            if state is ManagerState.UNKNOWN or probe.state is ProbeState.CONFLICT or time.monotonic() >= deadline:
                raise ServiceError("cannot prove the personal service stopped; maintenance must not continue")  # noqa: TRY003
            self._sleep(0.1)

    def _start_registered(self, definition: ServiceDefinition | None) -> None:
        if definition is None:
            raise ServiceError("the installed service has no definition")  # noqa: TRY003
        if not Path(definition.python_executable).is_file():
            raise ServiceError("the registered service executable is unavailable")  # noqa: TRY003
        try:
            self._adapter.resume()
            self._adapter.start(reload_definition=False)
            result = self._wait_until_live(definition.endpoint)
            if result.state is not ProbeState.LIVE:
                raise ServiceError("the registered service did not become live")  # noqa: TRY003, TRY301
            loaded = self._adapter.loaded_registration()
            if loaded.state is not ManagerOwnershipState.OWNED or loaded.definition != definition:
                raise ServiceError("the running service does not use the confirmed executable and configuration")  # noqa: TRY003, TRY301
            deadline = time.monotonic() + _START_TIMEOUT_SECONDS
            while True:
                readiness = self._readiness_probe(definition.endpoint)
                if readiness.state is ProbeState.LIVE:
                    return
                if readiness.state is ProbeState.CONFLICT or time.monotonic() >= deadline:
                    raise ServiceError(f"the service failed readiness: {readiness.detail}")  # noqa: TRY003, TRY301
                self._sleep(0.1)
        except (OSError, ServiceError):
            # Restore the guard even if startup partially succeeded. Never fall
            # back to an old executable after the database changed.
            self._adapter.suspend(self.maintenance_path)
            self._adapter.stop()
            raise

    def _maintenance_record(self) -> dict[str, object] | None:
        path = self.maintenance_path
        if not path.exists():
            if path.is_symlink():
                raise ServiceError("the service maintenance record must not be a symlink")  # noqa: TRY003
            return None
        if path.is_symlink() or not path.is_file():
            raise ServiceError("the service maintenance record is not a regular file")  # noqa: TRY003
        try:
            result = json.loads(path.read_text())
        except (OSError, ValueError) as error:
            raise ServiceError("cannot read the service maintenance record") from error  # noqa: TRY003
        if not isinstance(result, dict) or not isinstance(result.get("phase"), str):
            raise ServiceError("the service maintenance record is invalid")  # noqa: TRY003
        return result

    def _write_maintenance_record(self, value: dict[str, object]) -> None:
        self._maintenance_record()
        atomic_write(self.maintenance_path, json.dumps(value, sort_keys=True).encode(), mode=0o600)

    def registration_status(self) -> ServiceStatus:
        """Inspect support, artifact, and definition without touching the manager or endpoint."""

        support, support_detail = self._adapter.platform_support()
        if support is SupportState.UNSUPPORTED:
            return ServiceStatus(
                support=support,
                registration=RegistrationState.UNKNOWN,
                definition=DefinitionState.UNKNOWN,
                manager=ManagerState.UNKNOWN,
                server_liveness=LivenessState.UNKNOWN,
                endpoint=None,
                log_location=None,
                recovery_action=None,
                detail=support_detail,
                manager_ownership=ManagerOwnershipState.UNKNOWN,
            )

        registration = self._adapter.inspect()
        if registration.state is not RegistrationState.INSTALLED or registration.definition is None:
            recovery = (
                "run `powercontext service install` to install the personal service"
                if registration.state is RegistrationState.NOT_INSTALLED
                else "inspect the native service artifact before retrying"
            )
            return ServiceStatus(
                support=support,
                registration=registration.state,
                definition=DefinitionState.UNKNOWN,
                manager=ManagerState.UNKNOWN,
                server_liveness=LivenessState.UNKNOWN,
                endpoint=None,
                log_location=self._adapter.log_location(None),
                recovery_action=recovery,
                detail=registration.detail or support_detail,
                manager_ownership=ManagerOwnershipState.UNKNOWN,
            )

        definition = registration.definition
        installed_version = version("powercontext")
        installed_definition = definition_state(
            definition,
            package_version=installed_version,
            python_executable=service_python_executable(),
        )
        return ServiceStatus(
            support=support,
            registration=registration.state,
            definition=installed_definition,
            manager=ManagerState.UNKNOWN,
            server_liveness=LivenessState.UNKNOWN,
            endpoint=definition.endpoint,
            log_location=self._adapter.log_location(definition),
            recovery_action=None,
            detail=registration.detail or support_detail,
            manager_ownership=ManagerOwnershipState.UNKNOWN,
        )

    def status(self) -> ServiceStatus:
        support, support_detail = self._adapter.support()
        if support is SupportState.UNSUPPORTED:
            return ServiceStatus(
                support=support,
                registration=RegistrationState.UNKNOWN,
                definition=DefinitionState.UNKNOWN,
                manager=ManagerState.UNKNOWN,
                server_liveness=LivenessState.UNKNOWN,
                endpoint=None,
                log_location=None,
                recovery_action=None,
                detail=support_detail,
                manager_ownership=ManagerOwnershipState.UNKNOWN,
            )
        registration = self.registration_status()
        if registration.registration is not RegistrationState.INSTALLED:
            return registration

        loaded = self._adapter.loaded_registration()
        if loaded.state is ManagerOwnershipState.OWNED:
            manager = self._adapter.manager_state()
        elif loaded.state is ManagerOwnershipState.NOT_LOADED:
            manager = ManagerState.INACTIVE
        else:
            manager = ManagerState.UNKNOWN
        probe = self._probe(registration.endpoint or "")
        liveness = LivenessState.LIVE if probe.state is ProbeState.LIVE else LivenessState.UNREACHABLE
        recovery = _recovery_action(registration.definition, manager, probe, loaded)
        details = [detail for detail in (loaded.detail, probe.detail) if detail]
        return replace(
            registration,
            manager=manager,
            manager_ownership=loaded.state,
            server_liveness=liveness,
            recovery_action=recovery,
            detail="; ".join(details) or None,
        )

    def uninstall(self) -> ServiceStatus:
        support, detail = self._adapter.support()
        if support is SupportState.UNSUPPORTED:
            raise ServiceError(detail)
        with _service_lock(self._adapter.lock_path):
            registration = self._adapter.inspect()
            self._require_mutable_registration(registration)
            loaded = self._adapter.loaded_registration()
            self._require_mutable_manager_registration(loaded)
            if (
                registration.state is RegistrationState.NOT_INSTALLED
                and loaded.state is ManagerOwnershipState.NOT_LOADED
            ):
                return self.status()
            self._run_uninstall_stage("stop", self._adapter.stop)
            self._run_uninstall_stage("disable", self._adapter.disable)
            self._run_uninstall_stage("remove", self._remove_owned_artifact)
            self._run_uninstall_stage("reload", self._adapter.reload)
            self.maintenance_path.unlink(missing_ok=True)
        return self.status()

    def _build_definition(self, env_file: Path | None, *, start_on_login: bool) -> ServiceDefinition:
        try:
            loaded_env = load_protected_environment_file(env_file) if env_file is not None else None
        except ProtectedEnvironmentFileError as error:
            raise ServiceError(  # noqa: TRY003
                f"invalid personal service configuration: {error}", exit_code=2
            ) from error
        if loaded_env is None:
            inherited = sorted(
                name
                for name in os.environ
                if name == "POWERCONTEXT_HOME" or name.startswith(_PERSISTED_ENVIRONMENT_PREFIX)
            )
            if inherited:
                raise ServiceError(
                    _environment_file_guidance(inherited),
                    exit_code=2,
                )
        clean_home_context = (
            environment_context({}, clear={POWERCONTEXT_HOME_ENV}) if loaded_env is not None else nullcontext()
        )
        try:
            with (
                clean_home_context,
                server_settings_context(environment=loaded_env.values if loaded_env is not None else None) as settings,
            ):
                host = settings.http.host
                if not is_loopback_host(host):
                    raise ServiceError(  # noqa: TRY003
                        "personal services require a loopback Server bind", exit_code=2
                    )
                endpoint = _endpoint(host, settings.http.port)
                data_dir = str(powercontext_data_dir())
        except (ProtectedEnvironmentFileError, ServerConfigurationError) as error:
            raise ServiceError(  # noqa: TRY003
                f"invalid personal service configuration: {error}", exit_code=2
            ) from error

        return ServiceDefinition(
            ownership=OWNERSHIP_MARKER,
            definition_version=DEFINITION_VERSION,
            package_version=version("powercontext"),
            python_executable=service_python_executable(),
            endpoint=endpoint,
            data_dir=data_dir,
            env_file=loaded_env.identity if loaded_env is not None else None,
            start_on_login=start_on_login,
        )

    def _run_uninstall_stage(self, stage: str, operation: Callable[[], None]) -> None:
        try:
            operation()
        except (OSError, ServiceError) as error:
            try:
                status = self.status()
                artifact = self._adapter.inspect()
                remaining = (
                    f"remaining artifact: {self._adapter.artifact_path}; "
                    if artifact.state is not RegistrationState.NOT_INSTALLED
                    else "artifact removed; "
                )
                recovery = remaining + f"run `{self._adapter.uninstall_recovery(stage)}`"
                status = replace(status, recovery_action=recovery)
            except Exception:
                status = None
            raise ServiceError(  # noqa: TRY003
                f"personal service uninstall failed during {stage}: {error}",
                exit_code=error.exit_code if isinstance(error, ServiceError) else 1,
                status=status,
            ) from error

    def _post_commit_error(self, message: str, *, exit_code: int = 1) -> ServiceError:
        try:
            status = self.status()
        except Exception:
            status = None
        return ServiceError(message, exit_code=exit_code, status=status)

    def _commit_definition(self, definition: ServiceDefinition, previous: bytes | None) -> None:
        self._require_mutable_manager_registration(self._adapter.loaded_registration())
        content = self._adapter.render(definition)
        try:
            self._adapter.write(content)
            self._adapter.reload()
            self._adapter.enable()
        except BaseException:
            with suppress(Exception):
                self._adapter.disable()
            with suppress(Exception):
                self._adapter.restore(previous)
                self._adapter.reload()
                if previous is not None:
                    self._adapter.enable()
            raise

    def _remove_owned_artifact(self) -> None:
        registration = self._adapter.inspect()
        if registration.state is RegistrationState.NOT_INSTALLED:
            return
        self._require_mutable_registration(registration)
        self._adapter.remove()

    def _wait_until_live(self, endpoint: str) -> ProbeResult:
        # Wait on the authority that can tell the two outcomes apart: a native job that is
        # still running is still making progress, while a job that stopped will never
        # answer. The wall-clock budget only bounds the wait until that authority is
        # consulted, so a slow first start on a slow machine no longer fails by itself.
        started = time.monotonic()
        grace_deadline = started + _START_TIMEOUT_SECONDS + _START_GRACE_SECONDS
        check_manager_at = started + _START_TIMEOUT_SECONDS
        delay = 0.1
        result = self._probe(endpoint)
        while result.state is ProbeState.UNREACHABLE:
            now = time.monotonic()
            if now >= grace_deadline:
                break
            if now >= check_manager_at:
                if self._adapter.manager_state() is not ManagerState.ACTIVE:
                    break
                check_manager_at = min(now + _MANAGER_RECHECK_SECONDS, grace_deadline)
            self._sleep(delay)
            delay = min(delay * 2, 1.0)
            result = self._probe(endpoint)
        return result

    @staticmethod
    def _require_mutable_registration(registration: NativeRegistration) -> None:
        if registration.state in {RegistrationState.INVALID, RegistrationState.UNKNOWN}:
            raise ServiceError(registration.detail or "the native service registration cannot be safely modified")

    @staticmethod
    def _require_mutable_manager_registration(registration: ManagerRegistration) -> None:
        if registration.state in {ManagerOwnershipState.FOREIGN, ManagerOwnershipState.UNKNOWN}:
            raise ServiceError(registration.detail or "the native manager registration cannot be safely modified")


@contextmanager
def _service_lock(path: Path, *, timeout: float = 5.0) -> Generator[None, None, None]:
    if os.name == "nt":
        with _windows_service_lock(path, timeout=timeout):
            yield
        return

    import fcntl

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ServiceError(  # noqa: TRY003
                        "another PowerContext service operation is still running"
                    ) from None
                time.sleep(0.05)
        yield
    finally:
        with suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@contextmanager
def _windows_service_lock(path: Path, *, timeout: float) -> Generator[None, None, None]:
    import msvcrt

    # These Windows-only members are missing from the stdlib type stubs.
    msvcrt_members = vars(msvcrt)
    locking = cast(Callable[[int, int, int], None], msvcrt_members["locking"])
    lock_nonblocking = cast(int, msvcrt_members["LK_NBLCK"])
    lock_unlock = cast(int, msvcrt_members["LK_UNLCK"])
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
        deadline = time.monotonic() + timeout
        while True:
            try:
                os.lseek(descriptor, 0, os.SEEK_SET)
                locking(descriptor, lock_nonblocking, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise ServiceError(  # noqa: TRY003
                        "another PowerContext service operation is still running"
                    ) from None
                time.sleep(0.05)
        yield
    finally:
        with suppress(OSError):
            os.lseek(descriptor, 0, os.SEEK_SET)
            locking(descriptor, lock_unlock, 1)
        os.close(descriptor)


def _environment_file_guidance(names: list[str]) -> str:
    # Only display values known not to carry credentials; other settings may
    # include tokens or database URLs with embedded passwords.
    visible = {"POWERCONTEXT_HOME", "POWERCONTEXT_SERVER_HTTP_HOST", "POWERCONTEXT_SERVER_HTTP_PORT"}
    assignments = "\n".join(
        f"  {name}={shlex.quote(os.environ[name]) if name in visible else '<your-current-value>'}" for name in names
    )
    hidden_hint = (
        "Fill in <your-current-value> with your actual value.\n" if any(n not in visible for n in names) else ""
    )
    return (
        "personal services do not copy shell environment variables. Currently set:\n"
        f"{assignments}\n"
        "Save these settings in powercontext.env (UTF-8, owned by you with access restricted to you).\n"
        f"{hidden_hint}"
        'Then run: powercontext service install --env-file "<path-to-powercontext.env>"'
    )


def _endpoint(host: str, port: int) -> str:
    normalized = host.strip("[]")
    rendered_host = f"[{normalized}]" if ":" in normalized else normalized
    return f"http://{rendered_host}:{port}"


def _recovery_action(
    definition: DefinitionState,
    manager: ManagerState,
    probe: ProbeResult,
    loaded: ManagerRegistration,
) -> str | None:
    if definition in {DefinitionState.STALE, DefinitionState.MISSING_EXECUTABLE}:
        return "run `powercontext service install` to reconcile the installed definition"
    if loaded.state in {ManagerOwnershipState.FOREIGN, ManagerOwnershipState.UNKNOWN}:
        return "inspect the native manager registration; its PowerContext ownership could not be verified"
    if manager in {ManagerState.FAILED, ManagerState.INACTIVE}:
        return "inspect the native service logs, then run `powercontext service install`"
    if probe.state is ProbeState.CONFLICT:
        return "stop the conflicting listener or change the configured loopback port"
    if probe.state is ProbeState.UNREACHABLE:
        return "inspect the native service logs, then run `powercontext service install`"
    return None


__all__ = ["ServiceController", "ServiceMaintenanceSession", "ServiceMaintenanceSummary"]
