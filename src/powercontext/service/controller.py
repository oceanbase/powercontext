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
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager, nullcontext, suppress
from dataclasses import dataclass, replace
from importlib.metadata import version
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from powercontext.env_file import environment_context
from powercontext.paths import POWERCONTEXT_HOME_ENV, powercontext_data_dir
from powercontext.server.configuration import ServerConfigurationError, server_settings_context
from powercontext.service.adapters import NativeServiceAdapter, native_service_adapter
from powercontext.service.adapters.base import atomic_write, definition_state, service_python_executable
from powercontext.service.environment import (
    ProtectedEnvironmentFileError,
    load_protected_environment_file,
    read_protected_file,
    write_protected_file,
)
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
    ServiceTarget,
    SupportState,
)
from powercontext.service.native_command import native_command_boundary, native_command_evidence
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
        target: ServiceTarget | None = None,
    ) -> None:
        self._adapter = native_service_adapter() if adapter is None else adapter
        self._probe = probe
        if readiness_probe is None:
            from powercontext.service.probe import probe_readiness

            readiness_probe = probe_readiness
        self._readiness_probe = readiness_probe
        self._sleep = sleep
        self._target = target
        self._mutation_lock = threading.RLock()
        self._mutation_depth = 0

    @contextmanager
    def mutation(self, *, timeout: float = 10.0) -> Generator[None, None, None]:
        """Serialize revalidation, durable operation acceptance, and native changes.

        Nested operations on this controller share the OS lock. Other threads
        must acquire the instance lock before inspecting its nesting depth.
        """
        if not self._mutation_lock.acquire(timeout=timeout):
            raise ServiceError("another PowerContext service operation is still running")  # noqa: TRY003
        try:
            lock = (
                _service_lock(self._adapter.lock_path, timeout=timeout) if self._mutation_depth == 0 else nullcontext()
            )
            with lock:
                boundary = (
                    native_command_boundary(self.native_command_path) if self._mutation_depth == 0 else nullcontext()
                )
                with boundary:
                    self._mutation_depth += 1
                    try:
                        yield
                    finally:
                        self._mutation_depth -= 1
        finally:
            self._mutation_lock.release()

    def _server_target(self, *, require_exists: bool = False) -> ServiceTarget:
        target = self._target or ServiceTarget(
            service_python_executable(require_exists=require_exists), version("powercontext")
        )
        if require_exists and not Path(target.python_executable).is_file():
            raise ServiceError("the target Server executable is unavailable", exit_code=2)  # noqa: TRY003
        if require_exists and sys.platform == "win32" and Path(target.python_executable).name.lower() != "pythonw.exe":
            raise ServiceError("Windows personal services require the target pythonw.exe", exit_code=2)  # noqa: TRY003
        return target

    def install(
        self, *, env_file: Path | None = None, start_on_login: bool = True, require_ready: bool = False
    ) -> ServiceStatus:
        if not start_on_login and sys.platform != "win32":
            raise ServiceError(  # noqa: TRY003
                "disabling login auto-start is currently supported only on Windows",
                exit_code=2,
            )
        self._require_support()
        definition = self.preview_definition(env_file, start_on_login=start_on_login)
        initial_probe = self._probe(definition.endpoint)
        if initial_probe.state is ProbeState.CONFLICT:
            raise ServiceError(  # noqa: TRY003
                f"refusing to install over another listener: {initial_probe.detail}"
            )

        with self.mutation():
            record = self._maintenance_record()
            if record is not None and not (require_ready and record.get("phase") == "start_failed"):
                if record.get("phase") not in {"manual_stopped", "manual_switching"}:
                    raise ServiceError(  # noqa: TRY003
                        "the personal service is stopped for maintenance; finish migration or use service start first"
                    )
                self._recover_definition_switch()
                self._update_manually_stopped_definition(definition)
                return self.status()
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
            if require_ready:
                return self._install_ready(
                    definition,
                    registration,
                    changed=changed,
                    loaded_changed=loaded_changed,
                    manager_before=manager_before,
                    pending=record is not None,
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

    def _install_ready(
        self,
        definition: ServiceDefinition,
        registration: NativeRegistration,
        *,
        changed: bool,
        loaded_changed: bool,
        manager_before: ManagerState,
        pending: bool,
    ) -> ServiceStatus:
        """Register with a durable activation guard and verify business readiness."""
        if not changed and not loaded_changed and manager_before is ManagerState.ACTIVE and not pending:
            if self._readiness_probe(definition.endpoint).state is not ProbeState.LIVE:
                raise ServiceError("the running service is not ready; inspect its diagnostics")  # noqa: TRY003
            self._require_running_definition(definition)
            return self.status()
        # Keep a persistent guard before replacing the registration or
        # enabling native activation. A killed installer leaves evidence
        # for explicit recovery instead of an apparently complete start.
        self._write_maintenance_record({"phase": "start_failed"})
        self._adapter.suspend(self.maintenance_path)
        self._adapter.stop()
        if registration.definition is not None:
            self._require_stopped(registration.definition)
        self._require_stopped(definition)
        self._adapter.write(self._adapter.render(definition))
        self._adapter.update_suspended()
        self._start_registered(definition)
        self.maintenance_path.unlink(missing_ok=True)
        return self.status()

    @property
    def maintenance_path(self) -> Path:
        return self._adapter.lock_path.with_name(f"{self._adapter.lock_path.name}.maintenance.json")

    @property
    def native_command_path(self) -> Path:
        return self._adapter.lock_path.with_name(f"{self._adapter.lock_path.name}.native-command") / "state.json"

    @property
    def registration_removal_path(self) -> Path:
        return self._adapter.lock_path.with_name(f"{self._adapter.lock_path.name}.registration-removal") / "state.json"

    def _registration_removal(self) -> str | None:
        path = self.registration_removal_path
        if not path.exists() and not path.is_symlink():
            return None
        try:
            record = json.loads(read_protected_file(path).content)
            if (
                not isinstance(record, dict)
                or set(record) != {"format_version", "removal_id"}
                or type(record["format_version"]) is not int
                or record["format_version"] != 1
                or not isinstance(record["removal_id"], str)
                or str(UUID(record["removal_id"])) != record["removal_id"]
            ):
                raise ValueError("invalid registration removal evidence")  # noqa: TRY003, TRY301
            return record["removal_id"]
        except (OSError, ValueError) as error:
            raise ServiceError("the registration removal evidence cannot be verified") from error  # noqa: TRY003

    def start(self) -> ServiceStatus:
        """Start the registered executable without reinstalling or changing configuration."""

        with self.mutation():
            self._recover_definition_switch()
            registration, running = self._owned_registration()
            record = self._maintenance_record()
            if record is not None and record.get("phase") == "runtime_repair":
                raise ServiceError("resume the owning Ops runtime repair before starting the service")  # noqa: TRY003
            if record is not None and record.get("phase") not in {"manual_stopped", "database_ready", "start_failed"}:
                raise ServiceError(  # noqa: TRY003
                    "the database migration has not been verified; complete it before starting the service"
                )
            if running and record is None:
                definition = registration.definition
                if definition is None or self._readiness_probe(definition.endpoint).state is not ProbeState.LIVE:
                    raise ServiceError("the running service is not ready; inspect its diagnostics")  # noqa: TRY003
                self._require_running_definition(definition)
                return self.status()
            if record is None:
                # A failed start must also leave a durable activation guard;
                # systemd's condition alone is ineffective without this file.
                self._write_maintenance_record({"phase": "manual_stopped"})
            self._start_registered(registration.definition)
            self.maintenance_path.unlink(missing_ok=True)
        return self.status()

    def verify_running(self) -> ServiceStatus:
        """Explicitly verify readiness of an already running owned service.

        Recovery callers must not use ``start`` for this check: the native
        manager may stop independently after a previous status observation.
        """
        with self.mutation():
            registration, running = self._owned_registration()
            if not running or self._maintenance_record() is not None:
                raise ServiceError("the service is no longer running without a maintenance guard")  # noqa: TRY003
            definition = registration.definition
            if definition is None or self._readiness_probe(definition.endpoint).state is not ProbeState.LIVE:
                raise ServiceError("the running service is not ready; inspect its diagnostics")  # noqa: TRY003
            self._require_running_definition(definition)
            return self.status()

    def verify_stopped(self) -> ServiceStatus:
        """Verify an owned, guarded stop without changing native activation state."""
        with self.mutation():
            registration, running = self._owned_registration()
            if running or self._maintenance_record() is None:
                raise ServiceError("the service is not stopped with a persistent maintenance guard")  # noqa: TRY003
            self._require_stopped(registration.definition)
            return self.status()

    @contextmanager
    def runtime_repair(self, *, expected_definition: ServiceDefinition, repair_id: str) -> Generator[None, None, None]:
        """Hold an owned service stopped while its exact runtime is repaired.

        The installer owns resource publication and verification inside this
        boundary. A normal exit confirms those steps; an exception or process
        exit retains a guard that ordinary start/install cannot clear. Repair
        never changes the registered executable, configuration, or version,
        and completion leaves the service explicitly stopped.
        """
        if (
            not repair_id
            or len(repair_id) > 96
            or not all(c.isascii() and (c.isalnum() or c in "-_") for c in repair_id)
        ):
            raise ServiceError("the runtime repair identity is invalid")  # noqa: TRY003
        with self.mutation():
            registration, _running = self._owned_registration()
            if registration.definition != expected_definition:
                raise ServiceError("the service changed; review a new runtime repair plan")  # noqa: TRY003
            guard: dict[str, object] = {
                "phase": "runtime_repair",
                "repair_id": repair_id,
                "definition": expected_definition.as_dict(),
                "registration_checksum": hashlib.sha256(registration.content or b"").hexdigest(),
            }
            record = self._maintenance_record()
            if record is not None and record.get("phase") not in {"manual_stopped", "start_failed"} and record != guard:
                raise ServiceError("finish the existing service maintenance before repairing its runtime")  # noqa: TRY003
            self._write_maintenance_record(guard)
            self._adapter.suspend(self.maintenance_path)
            self._adapter.stop()
            self._require_stopped(expected_definition)
            yield
            registration, running = self._owned_registration()
            if (
                running
                or registration.definition != expected_definition
                or hashlib.sha256(registration.content or b"").hexdigest() != guard["registration_checksum"]
                or self._maintenance_record() != guard
            ):
                raise ServiceError("the service changed during runtime repair; preserve its recovery guard")  # noqa: TRY003
            self._require_stopped(expected_definition)
            if not Path(expected_definition.python_executable).is_file():
                raise ServiceError("the repaired Server executable is unavailable")  # noqa: TRY003
            self._write_maintenance_record({"phase": "manual_stopped"})

    def stop(self) -> ServiceStatus:
        """Stop the owned service and persistently suppress native automatic activation."""

        with self.mutation():
            record = self._maintenance_record()
            if record is not None and record.get("phase") in {
                "configuration_switching",
                "manual_switching",
                "switching",
            }:
                self._require_support()
                summary = self._validated_switch(record)
                self._adapter.suspend(self.maintenance_path)
                self._adapter.stop()
                self._require_stopped(summary.definition)
                self._require_stopped(summary.target_definition)
                return self.status()
            registration, _running = self._owned_registration()
            if record is None:
                self._write_maintenance_record({"phase": "manual_stopped"})
            self._adapter.suspend(self.maintenance_path)
            self._adapter.stop()
            self._require_stopped(registration.definition)
        return self.status()

    def restart(self) -> ServiceStatus:
        """Stop then start the registered executable; migration is a separate operation."""

        with self.mutation():
            self.stop()
            return self.start()

    def operation_snapshot(self) -> dict[str, object]:
        """Read exact native ownership and recovery evidence without a readiness probe.

        Call inside ``mutation`` when binding this evidence to a modification.
        Environment values and credentials are never included.
        """
        registration = self._adapter.inspect()
        loaded = self._adapter.loaded_registration()
        return {
            "identifier": self._adapter.identifier,
            "artifact_path": str(self._adapter.artifact_path),
            "registration": {
                "state": registration.state.value,
                "definition": registration.definition.as_dict() if registration.definition else None,
                "checksum": hashlib.sha256(registration.content or b"").hexdigest(),
            },
            "loaded": {
                "state": loaded.state.value,
                "definition": loaded.definition.as_dict() if loaded.definition else None,
            },
            "manager": self._adapter.manager_state().value,
            "maintenance": self._maintenance_record(),
            "native_command": native_command_evidence(self.native_command_path),
            "registration_removal": self._registration_removal(),
        }

    def configure(  # noqa: C901
        self,
        *,
        env_file: Path,
        start_on_login: bool = True,
        restart: bool = False,
        cancelled: Callable[[], bool] | None = None,
    ) -> ServiceStatus:
        """Switch to a protected immutable configuration revision under the service lock.

        The caller publishes a new private file; the active file is never
        overwritten. An interrupted switch requires this explicit operation to
        finish, and recovery preserves the stopped state.
        """
        if not start_on_login and sys.platform != "win32":
            raise ServiceError("disabling login auto-start is currently supported only on Windows", exit_code=2)  # noqa: TRY003
        with self.mutation():
            target = self.preview_definition(env_file, start_on_login=start_on_login)
            record = self._maintenance_record()
            if record is not None and record.get("phase") == "configuration_switching":
                previous_target = self._validated_switch(record).target_definition
                target = replace(target, data_dir=previous_target.data_dir)
                if previous_target != target:
                    raise ServiceError("the configuration revision changed; cannot resume the accepted switch")  # noqa: TRY003
                self._recover_definition_switch(configuration=True)
                return self.status()
            if record is not None and record.get("phase") != "manual_stopped":
                raise ServiceError("finish the existing service maintenance before changing configuration")  # noqa: TRY003
            registration, running = self._owned_registration()
            definition = registration.definition
            if definition is None:
                raise ServiceError("the installed service has no definition")  # noqa: TRY003
            target = replace(target, data_dir=definition.data_dir)
            if definition.env_file is not None and target.env_file is not None:
                if definition.env_file.path == target.env_file.path and definition.env_file != target.env_file:
                    raise ServiceError("configuration changes require a separate immutable revision")  # noqa: TRY003
                try:
                    load_protected_environment_file(Path(definition.env_file.path), expected=definition.env_file)
                except ProtectedEnvironmentFileError as error:
                    raise ServiceError("the registered environment file is not usable", exit_code=2) from error  # noqa: TRY003
            if registration.definition == target and registration.content == self._adapter.render(target):
                if restart and running and record is None and not (cancelled is not None and cancelled()):
                    return self.verify_running()
                return self.status()
            summary = ServiceMaintenanceSummary(
                self._adapter.identifier,
                str(self._adapter.artifact_path),
                definition,
                target,
                running and record is None,
                hashlib.sha256(registration.content or b"").hexdigest(),
            )
            self._write_maintenance_record({
                "phase": "configuration_switching",
                "summary": summary.as_dict(),
                "target_checksum": hashlib.sha256(self._adapter.render(target)).hexdigest(),
            })
            self._recover_definition_switch(configuration=True)
            if restart and summary.originally_running and not (cancelled is not None and cancelled()):
                self._start_registered(target)
                self.maintenance_path.unlink(missing_ok=True)
            return self.status()

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
            package_version=self._server_target().package_version,
            python_executable=self._server_target().python_executable,
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

        with self.mutation():
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
        self._write_maintenance_record({
            "phase": "switching",
            "summary": summary.as_dict(),
            "target_checksum": hashlib.sha256(self._adapter.render(summary.target_definition)).hexdigest(),
        })
        self._recover_definition_switch()
        if summary.originally_running:
            try:
                self._start_registered(summary.target_definition)
            except BaseException as error:
                # Catchable interruptions need the same explicit recovery state as startup errors.
                try:
                    self._write_maintenance_record({"phase": "start_failed", "summary": summary.as_dict()})
                except BaseException as record_error:
                    error.add_note(
                        f"could not record the failed startup: {type(record_error).__name__}: {record_error}"
                    )
                raise
            self.maintenance_path.unlink(missing_ok=True)
        return self.status()

    def _update_manually_stopped_definition(self, definition: ServiceDefinition) -> None:
        """Update an owned registration without undoing the user's explicit stop."""

        registration, _running = self._owned_registration()
        self._adapter.suspend(self.maintenance_path)
        self._adapter.stop()
        self._require_stopped(registration.definition)
        if registration.definition is None:
            raise ServiceError("the installed service has no definition")  # noqa: TRY003
        if definition.endpoint != registration.definition.endpoint:
            self._require_stopped(definition)
        target = self._adapter.render(definition)
        if registration.definition == definition and registration.content == target:
            return
        summary = ServiceMaintenanceSummary(
            self._adapter.identifier,
            str(self._adapter.artifact_path),
            registration.definition,
            definition,
            False,
            hashlib.sha256(registration.content or b"").hexdigest(),
        )
        self._write_maintenance_record({
            "phase": "manual_switching",
            "summary": summary.as_dict(),
            "target_checksum": hashlib.sha256(target).hexdigest(),
        })
        self._recover_definition_switch()

    def _recover_definition_switch(self, *, configuration: bool = False) -> None:
        """Resume a verified migration or explicit stopped-registration update."""
        record = self._maintenance_record()
        phases = {"switching", "manual_switching"}
        if configuration:
            phases.add("configuration_switching")
        if record is None or record.get("phase") not in phases:
            return
        configuration_update = record.get("phase") == "configuration_switching"
        manual_update = record.get("phase") == "manual_switching"
        summary = self._validated_switch(record)
        target = self._adapter.render(summary.target_definition)
        if summary.target_definition.env_file is not None:
            identity = summary.target_definition.env_file
            try:
                load_protected_environment_file(Path(identity.path), expected=identity)
            except ProtectedEnvironmentFileError as error:
                raise ServiceError("the pending service environment file is not usable", exit_code=2) from error  # noqa: TRY003
        self._adapter.suspend(self.maintenance_path)
        self._adapter.stop()
        self._require_stopped(summary.definition)
        if summary.target_definition.endpoint != summary.definition.endpoint:
            self._require_stopped(summary.target_definition)
        self._adapter.write(target)
        self._adapter.update_suspended()
        self._require_stopped(summary.target_definition)
        if manual_update or configuration_update:
            self._write_maintenance_record({"phase": "manual_stopped"})
        else:
            self._write_maintenance_record({"phase": "database_ready", "summary": summary.as_dict()})

    def _validated_switch(self, record: dict[str, object]) -> ServiceMaintenanceSummary:
        """Verify old/new definition evidence before recovering or stopping a switch."""
        try:
            payload = record["summary"]
            if not isinstance(payload, dict):
                raise TypeError("invalid summary")  # noqa: TRY003, TRY301
            payload = cast(dict[str, object], payload)
            originally_running = payload["originally_running"]
            if not isinstance(originally_running, bool):
                raise TypeError("invalid original service state")  # noqa: TRY003, TRY301
            if record.get("phase") == "manual_switching" and originally_running:
                raise ValueError("a manual registration update must preserve the stopped state")  # noqa: TRY003, TRY301
            summary = ServiceMaintenanceSummary(
                str(payload["identifier"]),
                str(payload["artifact_path"]),
                ServiceDefinition.from_dict(payload["definition"]),
                ServiceDefinition.from_dict(payload["target_definition"]),
                originally_running,
                str(payload["registration_checksum"]),
            )
            target = self._adapter.render(summary.target_definition)
            if (
                summary.identifier != self._adapter.identifier
                or summary.artifact_path != str(self._adapter.artifact_path)
                or summary.fingerprint != payload.get("fingerprint")
                or hashlib.sha256(target).hexdigest() != record.get("target_checksum")
            ):
                raise ValueError("changed switch intent")  # noqa: TRY003, TRY301
        except (KeyError, TypeError, ValueError) as error:
            raise ServiceError("the pending service switch evidence is invalid") from error  # noqa: TRY003
        registration = self._adapter.inspect()
        self._require_mutable_registration(registration)
        loaded = self._adapter.loaded_registration()
        self._require_mutable_manager_registration(loaded)
        if (
            registration.definition not in (summary.definition, summary.target_definition)
            or hashlib.sha256(registration.content or b"").hexdigest()
            not in (summary.registration_checksum, record["target_checksum"])
            or (
                loaded.state is ManagerOwnershipState.OWNED
                and loaded.definition not in (summary.definition, summary.target_definition)
            )
        ):
            raise ServiceError("the service registration changed outside the pending switch")  # noqa: TRY003
        return summary

    def _require_support(self) -> None:
        support, detail = self._adapter.support()
        if support is SupportState.UNSUPPORTED:
            raise ServiceError(detail)

    def _owned_registration(self) -> tuple[NativeRegistration, bool]:
        self._require_support()
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
            if time.monotonic() >= deadline:
                raise ServiceError(  # noqa: TRY003
                    f"cannot prove the personal service stopped; maintenance must not continue: "
                    f"manager={state.value}, probe={probe.state.value}, detail={probe.detail}"
                )
            self._sleep(0.1)

    def _require_running_definition(self, definition: ServiceDefinition) -> None:
        loaded = self._adapter.loaded_registration()
        if loaded.state is not ManagerOwnershipState.OWNED or loaded.definition != definition:
            raise ServiceError("the running service does not use the confirmed executable and configuration")  # noqa: TRY003
        if self._adapter.manager_state() is not ManagerState.ACTIVE:
            raise ServiceError("the managed service is not running; another listener may own the endpoint")  # noqa: TRY003

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
            deadline = time.monotonic() + _START_TIMEOUT_SECONDS
            while True:
                readiness = self._readiness_probe(definition.endpoint)
                if readiness.state is ProbeState.LIVE:
                    self._require_running_definition(definition)
                    return
                if readiness.state is ProbeState.CONFLICT or time.monotonic() >= deadline:
                    raise ServiceError(f"the service failed readiness: {readiness.detail}")  # noqa: TRY003, TRY301
                self._sleep(0.1)
        except BaseException as error:
            # Restore the guard even on Ctrl+C during startup verification, then
            # stop independently: failure to restore protection must not skip
            # stopping the process. Preserve the original failure and diagnostics.
            try:
                self._adapter.suspend(self.maintenance_path)
            except BaseException as suspend_error:
                error.add_note(
                    "automatic activation suppression could not be confirmed: "
                    f"{type(suspend_error).__name__}: {suspend_error}"
                )
            try:
                self._adapter.stop()
                self._require_stopped(definition)
            except BaseException as stop_error:
                error.add_note(
                    f"stopping the managed service could not be confirmed: {type(stop_error).__name__}: {stop_error}"
                )
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
        target = self._server_target()
        installed_definition = definition_state(
            definition,
            package_version=target.package_version,
            python_executable=target.python_executable,
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
        record = self._maintenance_record()
        if (
            loaded.state in {ManagerOwnershipState.OWNED, ManagerOwnershipState.NOT_LOADED}
            and manager in {ManagerState.INACTIVE, ManagerState.FAILED}
            and probe.state is ProbeState.UNREACHABLE
            and record is not None
        ):
            if record.get("phase") == "configuration_switching":
                recovery = "use the owning Ops caller to inspect and resume the interrupted `config_apply` operation"
            elif record.get("phase") == "runtime_repair":
                recovery = "use the owning Ops caller to inspect and resume the interrupted runtime repair"
            elif record.get("phase") == "manual_switching":
                recovery = "run `powercontext service install` to finish the interrupted stopped-registration update"
            elif record.get("phase") == "manual_stopped" and registration.definition is DefinitionState.CURRENT:
                recovery = "run `powercontext service start` when ready to resume the manually stopped service"
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
        self._require_support()
        with self.mutation():
            registration = self._adapter.inspect()
            self._require_mutable_registration(registration)
            loaded = self._adapter.loaded_registration()
            self._require_mutable_manager_registration(loaded)
            # Persist explicit removal intent before touching the registration.
            # A first install interrupted before registration is retryable, but
            # its recovery must never undo a later CLI or Ops removal.
            try:
                write_protected_file(
                    self.registration_removal_path,
                    json.dumps({"format_version": 1, "removal_id": str(uuid4())}).encode("utf-8"),
                )
            except (OSError, ValueError) as error:
                raise ServiceError("registration removal intent could not be recorded") from error  # noqa: TRY003
            if (
                registration.state is RegistrationState.NOT_INSTALLED
                and loaded.state is ManagerOwnershipState.NOT_LOADED
            ):
                self.maintenance_path.unlink(missing_ok=True)
                return self.status()
            self._run_uninstall_stage("stop", self._adapter.stop)
            self._run_uninstall_stage("disable", self._adapter.disable)
            self._run_uninstall_stage("remove", self._remove_owned_artifact)
            self._run_uninstall_stage("reload", self._adapter.reload)
            self.maintenance_path.unlink(missing_ok=True)
        return self.status()

    def preview_definition(self, env_file: Path | None = None, *, start_on_login: bool = True) -> ServiceDefinition:
        """Validate and describe the target definition without writing or starting it."""
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

        target = self._server_target(require_exists=True)
        return ServiceDefinition(
            ownership=OWNERSHIP_MARKER,
            definition_version=DEFINITION_VERSION,
            package_version=target.package_version,
            python_executable=target.python_executable,
            endpoint=endpoint,
            data_dir=data_dir,
            env_file=loaded_env.identity if loaded_env is not None else None,
            start_on_login=start_on_login,
            isolated_runtime=target.isolated_runtime,
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
