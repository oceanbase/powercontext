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

"""Fixed personal-service operations with explicit plans and durable outcomes."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from collections.abc import Callable
from contextlib import nullcontext, suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy.exc import ArgumentError

from powercontext.ops import protocol
from powercontext.ops.protocol import OpsError
from powercontext.ops.storage import OperationStore
from powercontext.server.configuration import ServerConfigurationError, server_settings_context
from powercontext.service.controller import ServiceController
from powercontext.service.environment import (
    load_protected_environment_file,
    protect_directory,
    read_protected_file,
    write_protected_file,
)
from powercontext.service.model import EnvironmentFileIdentity, ServiceDefinition, ServiceError, ServiceTarget
from powercontext.service.native_command import native_process_exited, observe_native_commands
from powercontext.transport import is_loopback_host

if TYPE_CHECKING:
    from powercontext.ops.installer import ManagedInstaller


@dataclass(frozen=True)
class TargetBinding:
    """Operator-owned identity, never selected by renderer request fields."""

    target_id: str
    python_executable: str
    package_version: str
    env_file: str
    configuration_owned: bool = False
    isolated_runtime: bool = False

    def __post_init__(self) -> None:
        protocol.identifier(self.target_id)
        if not Path(self.python_executable).is_absolute() or not Path(self.env_file).is_absolute():
            raise OpsError("target_invalid")
        if (
            not self.package_version
            or type(self.configuration_owned) is not bool
            or type(self.isolated_runtime) is not bool
        ):
            raise OpsError("target_invalid")

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if not self.isolated_runtime:
            payload.pop("isolated_runtime")
        return payload

    @classmethod
    def read(cls, path: Path) -> TargetBinding:
        value = protocol.decode(read_protected_file(path).content)
        optional = {"isolated_runtime"} if isinstance(value, dict) and "isolated_runtime" in value else set()
        payload = protocol.object_fields(
            value,
            {"format_version", "target_id", "python_executable", "package_version", "env_file", "configuration_owned"}
            | optional,
        )
        if type(payload["format_version"]) is not int or payload["format_version"] != 1:
            raise OpsError("target_unsupported")
        if not all(
            isinstance(payload[key], str) for key in ("target_id", "python_executable", "package_version", "env_file")
        ):
            raise OpsError("target_invalid")
        return cls(**{key: value for key, value in payload.items() if key != "format_version"})


class Ops:
    """One caller-authorized operation at a time, sharing native service locking.

    ``target`` and ``state_dir`` come from the trusted operator/distribution
    layer. The JSON request cannot supply executable, configuration, or state
    paths. An approval is meaningful only when the caller binds it to the plan
    shown to the user; this same-user process is not a privilege boundary.
    """

    def __init__(
        self,
        target: TargetBinding,
        state_dir: Path,
        *,
        controller: ServiceController | None = None,
        installer: ManagedInstaller | None = None,
    ) -> None:
        self.target = target
        self.installer = installer
        if installer is not None and (target != installer.target() or Path(state_dir) != installer.layout.state):
            raise OpsError("target_conflict")
        self.store = OperationStore(state_dir)
        self.controller = controller or ServiceController(
            target=ServiceTarget(
                target.python_executable, target.package_version, isolated_runtime=target.isolated_runtime
            ),
        )

    def handle(self, value: object, *, emit: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        """Validate a request, emit optional progress, and return one final message."""
        request_id = None
        try:
            request = protocol.request(value)
            request_id = request["request_id"]
            send = lambda message: emit(self._message(request_id, **message)) if emit else None
            result = self._dispatch(request, send)
            return self._message(request_id, **result)
        except OpsError as error:
            return self._message(
                request_id, outcome="rejected", error={"code": error.code}, recovery_actions=["inspect"]
            )
        except (OSError, ValueError, ServiceError, subprocess.SubprocessError, KeyError, TypeError):
            # Provider/configuration/native errors can contain secrets or paths.
            return self._message(
                request_id, outcome="rejected", error={"code": "inspection_failed"}, recovery_actions=["inspect"]
            )

    @staticmethod
    def _message(request_id: str | None, **fields: Any) -> dict[str, Any]:
        return {"protocol_version": 1, "request_id": request_id, "type": "result", **fields}

    def _dispatch(self, request: dict[str, Any], emit: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        action = request["action"]
        if action == "inspect":
            return {"outcome": "succeeded", "components": self.inspect()}
        if action == "config_validate":
            snapshot = self.controller.operation_snapshot()
            self._configuration(snapshot)
            return {"outcome": "succeeded", "components": {"configuration": "valid"}}
        if action == "preflight":
            return {"outcome": "succeeded", "plan": self.preflight(request["operation"], request["options"])}
        if action == "operation_status":
            return self._status(self._record(request["operation_id"]))
        if action == "cancel":
            record = self._record(request["operation_id"])
            attempt = record["attempts"][-1]
            if request["attempt"] != attempt["attempt"]:
                raise OpsError("attempt_conflict")
            if attempt["result"] is None:
                self.store.cancel(record["operation_id"], attempt["attempt"])
            return self._status(record)
        return self._apply(request, emit)

    def inspect(self) -> dict[str, Any]:
        status = self.controller.status()
        snapshot = self.controller.operation_snapshot()
        maintenance = snapshot["maintenance"]
        try:
            self._configuration(snapshot)
            configuration = "valid"
        except (OSError, ValueError):
            configuration = "missing_or_invalid"
        components: dict[str, Any] = {
            "support": status.support.value,
            "registration": status.registration.value,
            "definition": status.definition.value,
            "manager_ownership": status.manager_ownership.value,
            "manager": status.manager.value,
            "liveness": status.server_liveness.value,
            "readiness": "not_probed",
            "endpoint": status.endpoint,
            "maintenance": maintenance.get("phase") if isinstance(maintenance, dict) else None,
            "resources": "present" if Path(self.target.python_executable).is_file() else "missing_executable",
            "configuration": configuration,
        }
        if self.installer is not None:
            try:
                components.update(self.installer.inspect(self, snapshot))
            except (OSError, ValueError, ServiceError, KeyError, TypeError):
                components["installation_observation"] = "unknown"
        return components

    def _configuration_path(self, snapshot: dict[str, Any]) -> Path:
        definition = snapshot["registration"].get("definition")
        environment = definition.get("env_file") if definition else None
        return Path(environment["path"] if environment else self.target.env_file)

    def _configuration(self, snapshot: dict[str, Any]) -> dict[str, str]:
        path = self._configuration_path(snapshot)
        loaded = load_protected_environment_file(path)
        try:
            with server_settings_context(environment=loaded.values) as settings:
                if settings.http.uds is not None:
                    raise OpsError("configuration_transport_unsupported")
                if not is_loopback_host(settings.http.host):
                    raise OpsError("configuration_invalid")
        except (ServerConfigurationError, ArgumentError) as error:
            raise OpsError("configuration_invalid") from error
        return loaded.values

    def _assert_target(self, snapshot: dict[str, Any], operation: str) -> None:
        registration = snapshot["registration"]
        loaded = snapshot["loaded"]
        if registration["state"] in {"invalid", "unknown"} or loaded["state"] in {"foreign", "unknown"}:
            raise OpsError("ownership_conflict")
        for definition in (registration.get("definition"), loaded.get("definition")):
            if definition is not None and (
                os.path.normcase(definition["python_executable"]) != os.path.normcase(self.target.python_executable)
                or definition["package_version"] != self.target.package_version
                or definition.get("isolated_runtime", False) != self.target.isolated_runtime
            ):
                raise OpsError("ownership_conflict")
        if (
            operation not in {"register", "remove_registration", "install", "repair"}
            and registration["state"] != "installed"
        ):
            raise OpsError("registration_missing")

    def preflight(self, operation: object, options: object) -> dict[str, Any]:  # noqa: C901
        selected = protocol.options(operation, options)
        if operation in {"install", "repair"}:
            if self.installer is None:
                raise OpsError("managed_installation_unavailable")
            return self.installer.preflight(self, str(operation), selected)
        snapshot = self.controller.operation_snapshot()
        self._assert_target(snapshot, str(operation))
        identity = None
        if operation in {"register", "start", "restart", "config_apply"}:
            path = self._configuration_path(snapshot)
            loaded = load_protected_environment_file(path)
            identity = asdict(loaded.identity)
            self._configuration(snapshot)
            if not Path(self.target.python_executable).is_file():
                raise OpsError("runtime_damaged")
        if operation == "config_apply":
            path = self._configuration_path(snapshot)
            revisions = self.store.directory / "configurations"
            if not self.target.configuration_owned or (path != Path(self.target.env_file) and path.parent != revisions):
                raise OpsError("configuration_not_owned")
        if selected.get("start_on_login") is False and sys.platform != "win32":
            raise OpsError("option_unsupported")
        maintenance = snapshot["maintenance"]
        stopped = isinstance(maintenance, dict) and maintenance.get("phase") == "manual_stopped"
        may_start = operation in {"start", "restart"} or (operation == "register" and not stopped)
        if operation == "config_apply":
            switching = isinstance(maintenance, dict) and maintenance.get("phase") == "configuration_switching"
            may_start = (
                selected.get("restart", False) and snapshot["manager"] == "active" and not stopped and not switching
            )
        database = None
        if self.installer is not None and may_start:
            from powercontext.ops.local_policy import classify_database

            configuration = self.installer._configuration(self, snapshot, {})
            database = classify_database(configuration, self.target.package_version)
            if not database.allowed_to_start:
                raise OpsError(
                    "database_incompatible"
                    if database.classification == "incompatible"
                    else "database_compatibility_unknown"
                )
        result = {
            "protocol_version": 1,
            "target_id": self.target.target_id,
            "binding_id": protocol.digest(self.target.as_dict()),
            "operation": operation,
            "options": selected,
            "before": protocol.digest({
                "service": snapshot,
                "configuration": identity,
                **({"database": database.as_dict()} if database else {}),
            }),
            "effects": {"may_start": may_start, "preserves_data": True},
            "target_summary": self._target_summary(snapshot, str(operation), selected),
        }
        return {**result, "plan_id": protocol.digest(result)}

    def _target_summary(self, snapshot: dict[str, Any], operation: str, options: dict[str, Any]) -> dict[str, Any]:
        definition = snapshot["registration"].get("definition")
        login = options.get(
            "start_on_login", definition.get("start_on_login", True) if definition else sys.platform != "win32"
        )
        if operation == "register":
            definition = self.controller.preview_definition(
                env_file=self._configuration_path(snapshot), start_on_login=login
            ).as_dict()
        return {
            "python_executable": self.target.python_executable,
            "package_version": self.target.package_version,
            "env_file": str(self._configuration_path(snapshot)),
            "endpoint": definition.get("endpoint") if definition else None,
            "data_dir": definition.get("data_dir") if definition else None,
            "start_on_login": login,
        }

    def _record(self, operation_id: str) -> dict[str, Any]:
        record = self.store.operation(operation_id)
        if record is None:
            raise OpsError("operation_missing")
        if record.get("binding_id") != protocol.digest(self.target.as_dict()):
            raise OpsError("target_conflict")
        return record

    def _status(self, record: dict[str, Any], attempt: dict[str, Any] | None = None) -> dict[str, Any]:
        current = attempt or record["attempts"][-1]
        result = current["result"]
        try:
            observation = self.inspect()
        except (OSError, ValueError, ServiceError, subprocess.SubprocessError, KeyError, TypeError):
            observation = {"state": "unknown", "error": {"code": "inspection_failed"}}
        try:
            cancel_requested = self.store.cancelled(record["operation_id"], current["attempt"])
        except (OSError, ValueError):
            cancel_requested = None
        executor = current.get("executor")
        executor_observation = "unknown"
        if executor is not None:
            with suppress(OSError, ValueError):
                executor_observation = "exited" if native_process_exited(executor["pid"]) else "not_confirmed_exited"
        execution_state = "finished" if result else "unknown"
        if not result and executor_observation == "exited":
            execution_state = "interrupted"
        recovery_actions = [] if result and result["outcome"] == "succeeded" else ["inspect", "preflight"]
        if result and (result.get("error") or {}).get("code") == "checkpoint_failed":
            recovery_actions = ["operation_status"]
        return {
            "operation_id": record["operation_id"],
            "attempt": current["attempt"],
            "phase": current["phase"],
            "executor": {"pid": executor["pid"] if executor else None, "observation": executor_observation},
            "execution_state": execution_state,
            "outcome": result["outcome"] if result else "pending",
            "cancel_requested": cancel_requested,
            "result": result,
            "observation": observation,
            "history": [
                {"attempt": item["attempt"], "outcome": item["result"]["outcome"] if item["result"] else None}
                for item in record["attempts"]
            ],
            "recovery_actions": recovery_actions,
        }

    def _duplicate(self, request: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any] | None:
        record = self.store.operation(request["operation_id"])
        if record is None:
            if request["action"] == "resume":
                raise OpsError("operation_missing")
            return None
        record = self._record(request["operation_id"])
        attempts = record["attempts"]
        if request["action"] == "apply":
            if attempts[0]["plan"]["plan_id"] != plan["plan_id"]:
                raise OpsError("operation_conflict")
            return self._status(record)
        expected = request["expected_attempt"]
        if len(attempts) > expected:
            accepted = attempts[expected]
            if accepted["plan"]["plan_id"] != plan["plan_id"]:
                raise OpsError("operation_conflict")
            return self._status(record, accepted)
        if len(attempts) != expected:
            raise OpsError("attempt_conflict")
        return None

    def _apply(self, request: dict[str, Any], emit: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        plan = protocol.plan(request["plan"])
        protocol.authorize(request["authorization"], plan)
        if plan["binding_id"] != protocol.digest(self.target.as_dict()) or plan["target_id"] != self.target.target_id:
            raise OpsError("target_conflict")
        duplicate = self._duplicate(request, plan)
        if duplicate is not None:
            return duplicate
        managed = plan["operation"] in {"install", "repair"}
        with self.store.mutation(), nullcontext() if managed else self.controller.mutation(timeout=0):
            duplicate = self._duplicate(request, plan)
            if duplicate is not None:
                return duplicate
            record = self._new_attempt(request, plan)
            current = record["attempts"][-1]
            if self.preflight(plan["operation"], plan["options"]) != plan:
                raise OpsError("plan_stale")
            try:
                self.store.save(record)
                self.store.write("_latest.json", {"operation_id": record["operation_id"]})
            except (OSError, ValueError):
                # Acceptance can be durable even if its coordination pointer
                # could not be saved. Never label this an input rejection.
                outcome = "uncertain"
                recovery_actions = ["operation_status"]
                try:
                    if self.store.operation(record["operation_id"]) is None:
                        outcome = "failed"
                        recovery_actions = ["preflight"]
                except (OSError, ValueError):
                    pass
                return {
                    "operation_id": record["operation_id"],
                    "attempt": current["attempt"],
                    "outcome": outcome,
                    "error": {"code": "checkpoint_failed"},
                    "recovery_actions": recovery_actions,
                }
            try:
                emit({"type": "accepted", "operation_id": record["operation_id"], "attempt": current["attempt"]})
            except (OSError, ValueError, ServiceError, subprocess.SubprocessError, KeyError, TypeError):
                current["result"] = {"outcome": "failed", "error": {"code": "progress_unavailable"}}
                try:
                    self.store.save(record)
                except (OSError, ValueError):
                    current["result"] = {"outcome": "uncertain", "error": {"code": "checkpoint_failed"}}
                return self._status(record)
            self._execute(record, emit)
            return self._status(record)

    def _new_attempt(self, request: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:  # noqa: C901
        operation_id = request["operation_id"]
        recovery = None
        revision = None
        previous = None
        latest = self.store.read("_latest.json")
        latest_id = protocol.identifier(latest.get("operation_id")) if latest is not None else None
        latest_attempt = self._record(latest_id)["attempts"][-1]["attempt"] if latest_id is not None else None
        if request["action"] == "resume":
            record = self._record(operation_id)
            previous = record["attempts"][-1]
            self._require_native_settled(previous)
            if latest_id != operation_id and not (
                previous["phase"] == "accepted"
                and previous["result"] is None
                and "coordination_previous_operation_id" in previous
                and "coordination_previous_attempt" in previous
                and latest_id == previous["coordination_previous_operation_id"]
                and latest_attempt == previous["coordination_previous_attempt"]
            ):
                raise OpsError("operation_superseded")
            if previous["plan"]["operation"] != plan["operation"]:
                raise OpsError("operation_conflict")
            old_options = {key: value for key, value in previous["plan"]["options"].items() if key != "restart"}
            new_options = {key: value for key, value in plan["options"].items() if key != "restart"}
            if old_options != new_options or (
                plan["options"].get("restart", False) and not previous["plan"]["options"].get("restart", False)
            ):
                raise OpsError("operation_conflict")
            if previous["result"] is not None and previous["result"]["outcome"] == "succeeded":
                raise OpsError("operation_completed")
            if (
                plan["operation"] not in {"install", "repair"}
                and previous["phase"] == "executing"
                and (previous["result"] is None or previous["result"]["outcome"] == "uncertain")
            ):
                recovery = self._reconcile(previous, plan)
            revision = previous.get("configuration_revision")
            if len(record["attempts"]) >= 64:
                raise OpsError("attempt_limit")
        else:
            self._require_previous_settled(plan)
            record = {"operation_id": operation_id, "binding_id": plan["binding_id"], "attempts": []}
        record["attempts"].append({
            "attempt": len(record["attempts"]) + 1,
            "plan": plan,
            "phase": "accepted",
            "executor": {"pid": os.getpid()},
            "coordination_previous_operation_id": latest_id,
            "coordination_previous_attempt": latest_attempt,
            "result": None,
            "recovery": recovery,
        })
        if revision is not None:
            record["attempts"][-1]["configuration_revision"] = revision
        if plan["operation"] in {"install", "repair"}:
            if self.installer is None:
                raise OpsError("managed_installation_unavailable")
            record["attempts"][-1]["installation"] = self.installer.begin(self, previous, plan, operation_id)
        return record

    def _require_previous_settled(self, plan: dict[str, Any]) -> None:
        # Stopping/removing a proven owned registration remains available when
        # an earlier attempt needs investigation. Starting or switching never
        # bypasses an unresolved native mutation merely by changing request ID.
        latest = self.store.read("_latest.json")
        if latest is None:
            return
        record = self._record(protocol.identifier(latest.get("operation_id")))
        previous = record["attempts"][-1]
        self._require_native_settled(previous)
        if plan["operation"] in {"stop", "remove_registration"}:
            return
        if previous["phase"] == "executing" and (
            previous["result"] is None or previous["result"]["outcome"] == "uncertain"
        ):
            raise OpsError("outcome_uncertain")

    @staticmethod
    def _require_native_settled(attempt: dict[str, Any]) -> None:
        native = attempt.get("native_command")
        if native is None or native["phase"] in {"completed", "spawn_failed"}:
            return
        if native["phase"] == "dispatching":
            raise OpsError("outcome_uncertain")
        if not native_process_exited(native["pid"]):
            raise OpsError("operation_busy")

    def _reconcile(self, previous: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        """Use exact native postconditions, never process death, to reconcile."""
        snapshot: dict[str, Any] = self.controller.operation_snapshot()
        operation = previous["plan"]["operation"]
        self._assert_target(snapshot, operation)
        maintenance = snapshot["maintenance"]
        phase = maintenance.get("phase") if isinstance(maintenance, dict) else None
        registration = snapshot["registration"]
        loaded = snapshot["loaded"]
        inactive = snapshot["manager"] in {"inactive", "failed"}
        if operation == "stop" and inactive and phase == "manual_stopped":
            self.controller.verify_stopped()
            return {"action": "completed"}
        if (
            operation == "remove_registration"
            and inactive
            and registration["state"] == "not_installed"
            and loaded["state"] == "not_loaded"
        ):
            return {"action": "completed"}
        if (
            operation in {"start", "register"}
            and snapshot["manager"] == "active"
            and phase is None
            and loaded["state"] == "owned"
            and loaded["definition"] == registration["definition"]
        ):
            if operation == "register":
                definition = registration["definition"]
                requested_login = previous["plan"]["target_summary"]["start_on_login"]
                environment = definition.get("env_file")
                if definition.get("start_on_login", True) != requested_login or environment is None:
                    raise OpsError("outcome_uncertain")
                if (
                    load_protected_environment_file(Path(environment["path"])).identity
                    != ServiceDefinition.from_dict(definition).env_file
                ):
                    raise OpsError("plan_stale")
            # The confirmed running target is only checked for readiness;
            # it is never registered or restarted again.
            return {"action": "verify_running"}
        if operation == "config_apply":
            return self._reconcile_configuration(previous, snapshot, allow_readiness=plan["effects"]["may_start"])
        # A released Python lock does not establish that a native child exited.
        # No known postcondition means no replay, especially for restart.
        raise OpsError("outcome_uncertain")

    def _reconcile_configuration(
        self, previous: dict[str, Any], snapshot: dict[str, Any], *, allow_readiness: bool
    ) -> dict[str, Any]:
        revision = previous.get("configuration_revision")
        if not isinstance(revision, dict):
            raise OpsError("outcome_uncertain")
        revision = cast(dict[str, Any], revision)
        path = Path(revision["path"])
        if path.parent != self.store.directory / "configurations":
            raise OpsError("record_invalid")
        revision_identity = EnvironmentFileIdentity(**revision["identity"])
        if read_protected_file(path).identity != revision_identity:
            raise OpsError("plan_stale")
        definition = snapshot["registration"].get("definition")
        environment = ServiceDefinition.from_dict(definition).env_file if definition else None
        maintenance = snapshot["maintenance"]
        phase = maintenance.get("phase") if isinstance(maintenance, dict) else None
        login_matches = (
            "start_on_login" not in previous["plan"]["options"]
            or definition.get("start_on_login", True) == previous["plan"]["options"]["start_on_login"]
        )
        if environment == revision_identity and login_matches:
            if phase == "manual_stopped":
                try:
                    self.controller.verify_stopped()
                except (OSError, ValueError, ServiceError) as error:
                    raise OpsError("outcome_uncertain") from error
                return {"action": "completed"}
            loaded = snapshot["loaded"]
            if (
                phase is None
                and snapshot["manager"] == "active"
                and loaded["state"] == "owned"
                and loaded["definition"] == definition
                and allow_readiness
            ):
                return {"action": "verify_running"}
        if phase == "configuration_switching":
            target = cast(dict[str, Any], maintenance).get("summary", {}).get("target_definition", {})
            if target and ServiceDefinition.from_dict(target).env_file == revision_identity:
                return {"action": "configuration_switch", "revision": revision}
        raise OpsError("outcome_uncertain")

    def _execute(self, record: dict[str, Any], emit: Callable[[dict[str, Any]], None]) -> None:  # noqa: C901
        current = record["attempts"][-1]
        plan = current["plan"]
        operation_id = record["operation_id"]
        cancelled = lambda: self.store.cancelled(operation_id, current["attempt"])
        configuration_cancelled = False

        def cancelled_at_mutation_boundary() -> bool:
            nonlocal configuration_cancelled
            requested = cancelled()
            # configure invokes this only before an otherwise permitted
            # start/readiness stage. A later marker cannot rewrite success.
            configuration_cancelled = configuration_cancelled or requested
            return requested

        def native_checkpoint(value: dict[str, object]) -> None:
            current["native_command"] = value
            self.store.save(record)

        try:
            if plan["operation"] in {"install", "repair"}:
                if self.installer is None:
                    raise OpsError("managed_installation_unavailable")  # noqa: TRY301
                current["result"] = self.installer.execute(self, record, emit)
            elif cancelled():
                current["result"] = {"outcome": "cancelled", "components": self.inspect()}
            else:
                recovery = current.get("recovery")
                if plan["operation"] == "config_apply" and (
                    not recovery or recovery["action"] == "configuration_switch"
                ):
                    current["configuration_revision"] = (
                        recovery["revision"]
                        if recovery and recovery["action"] == "configuration_switch"
                        else self._prepare_configuration(plan)
                    )
                current["phase"] = "executing"
                self.store.save(record)
                try:
                    emit({
                        "type": "progress",
                        "operation_id": operation_id,
                        "attempt": current["attempt"],
                        "sequence": 1,
                        "phase": "service",
                    })
                except (OSError, ValueError, ServiceError, subprocess.SubprocessError, KeyError, TypeError):
                    # No native mutation has started. A broken progress channel
                    # must not turn this known failure into unresolved work.
                    current["phase"] = "finished"
                    current["result"] = {"outcome": "failed", "error": {"code": "progress_unavailable"}}
                    self.store.save(record)
                    return
                if cancelled():
                    current["result"] = {"outcome": "cancelled", "components": self.inspect()}
                    current["phase"] = "finished"
                    self.store.save(record)
                    return
                recovery = current.get("recovery")
                if recovery and recovery["action"] == "verify_running":
                    self.controller.verify_running()
                elif not recovery or recovery["action"] != "completed":
                    with observe_native_commands(native_checkpoint):
                        self._mutate(
                            plan,
                            cancelled_at_mutation_boundary,
                            current.get("configuration_revision"),
                            recovering=bool(recovery),
                        )
                components = self.inspect()
                if (
                    plan["effects"]["may_start"]
                    and components["manager"] == "active"
                    and not cancelled()
                    and (not recovery or recovery["action"] == "verify_running")
                ):
                    components["readiness"] = "verified_during_operation"
                current["result"] = {"outcome": "succeeded", "components": components}
                if configuration_cancelled:
                    current["result"]["outcome"] = "cancelled"
            current["phase"] = "finished"
        except OpsError as error:
            if error.code == "resource_cancelled" and plan["operation"] in {"install", "repair"}:
                current["result"] = {"outcome": "cancelled", "components": self.inspect()}
                current["phase"] = "finished"
            else:
                current["result"] = self._failure(current)
                current["result"]["error"] = {"code": error.code}
        except (OSError, ValueError, ServiceError, subprocess.SubprocessError, KeyError, TypeError):
            current["result"] = self._failure(current)
        try:
            self.store.save(record)
        except (OSError, ValueError):
            current["result"] = {"outcome": "uncertain", "error": {"code": "checkpoint_failed"}}

    def _failure(self, current: dict[str, Any]) -> dict[str, Any]:
        outcome = "failed" if current["phase"] != "executing" else "uncertain"
        components = None
        try:
            components = self.inspect()
            if (
                current["phase"] == "executing"
                and components["registration"] == "installed"
                and components["manager"] == "inactive"
                and components["maintenance"] in {"manual_stopped", "start_failed"}
            ):
                self.controller.verify_stopped()
                outcome = "partial_failure"
                current["phase"] = "finished"
                if current["plan"]["effects"]["may_start"]:
                    components["readiness"] = "failed"
        except (OSError, ValueError, ServiceError, subprocess.SubprocessError, KeyError, TypeError):
            pass
        return {
            "outcome": outcome,
            "components": components,
            "error": {"code": "outcome_uncertain" if outcome == "uncertain" else "operation_failed"},
            "recovery_actions": ["inspect", "preflight"],
        }

    def _prepare_configuration(self, plan: dict[str, Any]) -> dict[str, Any]:
        snapshot = self.controller.operation_snapshot()
        values = self._configuration(snapshot)
        options = plan["options"]
        for option, name in (
            ("http_port", "POWERCONTEXT_SERVER_HTTP_PORT"),
            ("dashboard_enabled", "POWERCONTEXT_SERVER_DASHBOARD_ENABLED"),
        ):
            if option in options:
                values = {key: value for key, value in values.items() if key.casefold() != name.casefold()}
                values[name] = str(options[option]).lower()
        with server_settings_context(environment=values):
            pass
        content = "".join(f"{key}={shlex.quote(value)}\n" for key, value in sorted(values.items())).encode("utf-8")
        directory = self.store.directory / "configurations"
        protect_directory(directory, create=True)
        path = directory / f"{protocol.digest(values)}.env"
        if path.exists():
            if read_protected_file(path).content != content:
                raise OpsError("configuration_conflict")
        else:
            write_protected_file(path, content)
        return {"path": str(path), "identity": asdict(read_protected_file(path).identity)}

    def _mutate(
        self, plan: dict[str, Any], cancelled: Callable[[], bool], revision: dict[str, Any] | None, *, recovering: bool
    ) -> None:
        operation = plan["operation"]
        options = plan["options"]
        if operation == "register":
            path = self._configuration_path(self.controller.operation_snapshot())
            self.controller.install(
                env_file=path, start_on_login=plan["target_summary"]["start_on_login"], require_ready=True
            )
        elif operation == "config_apply":
            snapshot: dict[str, Any] = self.controller.operation_snapshot()
            if revision is None:
                raise OpsError("record_invalid")
            path = Path(revision["path"])
            definition = snapshot["registration"]["definition"]
            self.controller.configure(
                env_file=path,
                start_on_login=options.get("start_on_login", definition.get("start_on_login", True)),
                restart=plan["effects"]["may_start"] and not recovering,
                cancelled=cancelled,
            )
        else:
            methods = {
                "start": self.controller.start,
                "stop": self.controller.stop,
                "restart": self.controller.restart,
                "remove_registration": self.controller.uninstall,
            }
            methods[operation]()
