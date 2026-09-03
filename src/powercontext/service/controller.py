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

import os
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext, suppress
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path

from powercontext.cli.env_file import environment_context
from powercontext.paths import POWERCONTEXT_HOME_ENV, powercontext_data_dir
from powercontext.server.configuration import ServerConfigurationError, server_settings_context
from powercontext.service.adapters import NativeServiceAdapter, native_service_adapter
from powercontext.service.adapters.base import definition_state
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
_START_TIMEOUT_SECONDS = 30.0


class ServiceController:
    def __init__(
        self,
        adapter: NativeServiceAdapter | None = None,
        *,
        probe: Callable[[str], ProbeResult] = probe_server,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._adapter = native_service_adapter() if adapter is None else adapter
        self._probe = probe
        self._sleep = sleep

    def install(self, *, env_file: Path | None = None) -> ServiceStatus:
        support, detail = self._adapter.support()
        if support is SupportState.UNSUPPORTED:
            raise ServiceError(detail)
        definition = self._build_definition(env_file)
        initial_probe = self._probe(definition.endpoint)
        if initial_probe.state is ProbeState.CONFLICT:
            raise ServiceError(  # noqa: TRY003
                f"refusing to install over another listener: {initial_probe.detail}"
            )

        with _service_lock(self._adapter.lock_path):
            registration = self._adapter.inspect()
            self._require_mutable_registration(registration)
            loaded = self._adapter.loaded_registration()
            self._require_mutable_manager_registration(loaded)
            changed = registration.definition != definition
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
                final_probe = self._wait_until_live(definition.endpoint)
                if final_probe.state is not ProbeState.LIVE:
                    raise self._post_commit_error(  # noqa: TRY003
                        f"the personal service was registered but did not become live: {final_probe.detail}; "
                        f"inspect {self._adapter.log_location(definition) or 'the native service logs'}"
                    )
        return self.status()

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
            python_executable=sys.executable,
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
        return self.status()

    def _build_definition(self, env_file: Path | None) -> ServiceDefinition:
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
                raise ServiceError(  # noqa: TRY003
                    "personal services do not copy shell environment variables; write the Server configuration "
                    "to a protected file and pass --env-file",
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
            python_executable=os.path.abspath(sys.executable),
            endpoint=endpoint,
            data_dir=data_dir,
            env_file=loaded_env.identity if loaded_env is not None else None,
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
        deadline = time.monotonic() + _START_TIMEOUT_SECONDS
        delay = 0.1
        result = self._probe(endpoint)
        while result.state is ProbeState.UNREACHABLE and time.monotonic() < deadline:
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
def _service_lock(path: Path, *, timeout: float = 5.0) -> Iterator[None]:
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


__all__ = ["ServiceController"]
