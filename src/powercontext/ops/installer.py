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

"""Fixed managed installation stages, checkpointed by the existing Ops journal."""

from __future__ import annotations

import os
import shutil
import socket
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlsplit
from uuid import uuid4

from powercontext.ops import protocol
from powercontext.ops.database_admission import admit_database, require_admitted
from powercontext.ops.local_policy import (
    classify_database,
    default_data_dir,
    inspect_existing_configuration,
    new_configuration,
    preview_new_configuration,
)
from powercontext.ops.protocol import OpsError
from powercontext.ops.resources import (
    ResourceCatalog,
    ResourceError,
    ResourceSpec,
    ResourceStore,
    validate_destination,
    validate_owned_tree,
    verify,
)
from powercontext.service.environment import protect_directory, read_protected_file, write_protected_file
from powercontext.service.model import EnvironmentFileIdentity, ServiceDefinition
from powercontext.service.native_command import observe_native_commands

if TYPE_CHECKING:
    from powercontext.ops.runtime import Ops, TargetBinding


@dataclass(frozen=True)
class ManagedLayout:
    programs: Path
    configuration: Path
    state: Path
    cache: Path
    data: Path

    def __post_init__(self) -> None:
        paths = [Path(value) for value in asdict(self).values()]
        if any(not path.is_absolute() or ".." in path.parts for path in paths) or any(
            a == b or a.is_relative_to(b) for i, a in enumerate(paths) for j, b in enumerate(paths) if i != j
        ):
            raise OpsError("layout_invalid")

    @classmethod
    def current_user(cls) -> ManagedLayout:
        if sys.platform == "win32":
            value = os.environ.get("LOCALAPPDATA")
            if not value or not Path(value).is_absolute():
                raise OpsError("layout_invalid")
            local = Path(value)
            root = local / "PowerContextManaged"
            return cls(
                local / "Programs/PowerContext/managed",
                root / "config",
                root / "state",
                root / "cache",
                default_data_dir(),
            )
        if sys.platform == "darwin":
            root = Path.home() / "Library/Application Support/PowerContextManaged"
            return cls(
                root / "programs",
                root / "config",
                root / "state",
                Path.home() / "Library/Caches/PowerContextManaged",
                default_data_dir(),
            )
        raise OpsError("unsupported_platform")

    def as_dict(self) -> dict[str, str]:
        return {key: str(value) for key, value in asdict(self).items()}


class ManagedInstaller:
    """Installation policy; operation ownership, locking and outcomes remain in Ops."""

    def __init__(self, catalog: ResourceCatalog, spec: ResourceSpec, layout: ManagedLayout) -> None:
        if spec not in catalog.resources:
            raise OpsError("resource_untrusted")
        self.catalog = catalog
        self.spec = spec
        self.layout = layout
        self.directory = layout.programs / "server" / spec.resource_id
        self.environment = layout.configuration / "server.env"
        self.resources = ResourceStore(layout.cache)

    def target(self) -> TargetBinding:
        from powercontext.ops.runtime import TargetBinding

        return TargetBinding(
            f"managed-{self.spec.digest}",
            str(self.directory / self.spec.python_relative),
            self.spec.server_version,
            str(self.environment),
            True,
            isolated_runtime=True,
        )

    def _installation(self, ops: Ops) -> dict[str, Any] | None:
        record = ops.store.read("_installation.json")
        if record is None:
            return None
        expected = {
            "format_version",
            "installation_id",
            "binding_id",
            "resource_digest",
            "resource_identity",
            "configuration_identity",
            "definition",
        }
        if (
            set(record) != expected
            or type(record.get("format_version")) is not int
            or record["format_version"] != 1
            or record.get("binding_id") != protocol.digest(ops.target.as_dict())
            or record.get("resource_digest") != self.spec.digest
        ):
            raise OpsError("installation_record_invalid")
        protocol.identifier(record["installation_id"])
        if not isinstance(record["resource_identity"], dict) or not isinstance(record["configuration_identity"], dict):
            raise OpsError("installation_record_invalid")
        definition = ServiceDefinition.from_dict(record["definition"])
        if (
            definition.python_executable != ops.target.python_executable
            or definition.package_version != self.spec.server_version
            or not definition.isolated_runtime
        ):
            raise OpsError("installation_record_invalid")
        return record

    def _pending(self, ops: Ops, *, snapshot: dict[str, Any] | None = None) -> dict[str, Any] | None:
        latest = ops.store.read("_installation_owner.json")
        if latest is None:
            return None
        if set(latest) != {"format_version", "operation_id"}:
            raise OpsError("installation_record_invalid")
        record = ops._record(protocol.identifier(latest.get("operation_id")))
        if snapshot is not None and snapshot["registration"].get("definition") is None:
            removal = snapshot["registration_removal"]
            for previous in record["attempts"]:
                result = previous.get("result") or {}
                components = result.get("components") or {}
                state = previous.get("installation") or {}
                facts = previous["plan"].get("installation") or {}
                if (
                    facts.get("registration_present") is True
                    or state.get("stage") == "complete"
                    or components.get("registration") == "installed"
                    or facts.get("registration_removal") != removal
                    or (
                        "registration_removal" not in facts
                        and (
                            state.get("stage") == "registration_dispatching"
                            or previous.get("native_command") is not None
                        )
                    )
                ):
                    raise OpsError("registration_missing")
        attempt = record["attempts"][-1]
        result = attempt.get("result")
        return attempt.get("installation") if result is None or result.get("outcome") != "succeeded" else None

    def _configuration(self, ops: Ops, snapshot: dict[str, Any], options: dict[str, Any]):
        definition = snapshot["registration"].get("definition")
        path = ops._configuration_path(snapshot)
        if path != self.environment and path.parent != self.layout.state / "configurations":
            raise OpsError("configuration_not_owned")
        if path.exists() or path.is_symlink():
            summary = inspect_existing_configuration(
                path, data_dir=Path(definition["data_dir"]) if definition else self.layout.data
            )
            if any(
                data.is_relative_to(root)
                for data in (summary.data_dir, summary.database_path)
                for root in (self.layout.programs, self.layout.configuration, self.layout.state, self.layout.cache)
            ):
                raise OpsError("configuration_path_unsupported")
            if "http_port" in options and urlsplit(summary.endpoint).port != options["http_port"]:
                raise OpsError("configuration_conflict")
            return summary
        if definition is not None:
            raise OpsError("configuration_invalid")
        return preview_new_configuration(self.layout.data, options.get("http_port", 8000))

    def _resource_fact(
        self, evidence: dict[str, Any] | None, *, cancelled: Callable[[], bool] = lambda: False
    ) -> tuple[str, dict[str, int] | None]:
        if not self.directory.exists() and not self.directory.is_symlink():
            return "missing", None
        identity = validate_owned_tree(self.directory)
        prepared = evidence.get("prepared") if evidence else None
        owned = evidence is not None and (
            identity == evidence.get("resource_identity")
            or (isinstance(prepared, dict) and identity == prepared.get("identity"))
        )
        if not owned:
            raise OpsError("ownership_conflict")
        try:
            verify(self.spec, self.directory, cancelled=cancelled)
        except ResourceError as error:
            if error.code != "resource_corrupt":
                raise
            return "damaged", identity
        return "verified", identity

    def _repair_owner(self, ops: Ops, snapshot: dict[str, Any], evidence: dict[str, Any] | None) -> str | None:
        guard = snapshot["maintenance"]
        if not isinstance(guard, dict) or guard.get("phase") != "runtime_repair":
            return None
        owner = protocol.identifier(guard.get("repair_id"))
        original = ops._record(owner)["attempts"][-1]
        proof = original.get("installation")
        if (
            evidence is None
            or not isinstance(proof, dict)
            or proof.get("repair_owner") != owner
            or evidence.get("repair_owner") != owner
            or proof["installation_id"] != evidence["installation_id"]
            or original["plan"]["operation"] != "repair"
            or original["plan"]["installation"]["resource"]["resource_digest"] != self.spec.digest
            or guard.get("definition") != snapshot["registration"].get("definition")
            or guard.get("registration_checksum") != snapshot["registration"].get("checksum")
        ):
            raise OpsError("repair_ownership_conflict")
        ops._require_native_settled(original)
        return owner

    def admit_start(self, ops: Ops, snapshot: dict[str, Any]):
        """Verify the owned target before delegating database admission to it."""
        evidence = self._installation(ops) or self._pending(ops, snapshot=snapshot)
        resources, identity = self._resource_fact(evidence)
        if resources != "verified":
            raise OpsError("runtime_damaged")
        database = admit_database(
            self._configuration(ops, snapshot, {}),
            self.spec.server_version,
            python_executable=self.directory / self.spec.python_console_relative,
        )
        require_admitted(database)
        return replace(
            database,
            fingerprint=protocol.digest({
                "database": database.fingerprint,
                "resource_digest": self.spec.digest,
                "resource_identity": identity,
                "catalog": self.catalog.sha256,
            }),
        )

    def preflight(self, ops: Ops, operation: str, options: dict[str, Any]) -> dict[str, Any]:  # noqa: C901
        validate_destination(self.spec, self.directory)
        validate_destination(self.spec, self.layout.programs / "staging" / ("resource-" + "0" * 32))
        snapshot: dict[str, Any] = ops.controller.operation_snapshot()
        ops._assert_target(snapshot, operation)
        installation = self._installation(ops)
        definition = snapshot["registration"].get("definition")
        pending = self._pending(ops, snapshot=snapshot)
        evidence = installation or pending
        if installation and pending and installation["installation_id"] == pending["installation_id"]:
            evidence = {**installation, **pending}
        if evidence is None and (definition is not None or self.environment.exists()):
            raise OpsError("existing_independent")
        if operation == "repair" and installation is None:
            raise OpsError("installation_missing")
        if installation is not None and definition is None:
            raise OpsError("registration_missing")
        repair_owner = self._repair_owner(ops, snapshot, evidence)
        if repair_owner is not None and operation != "repair":
            raise OpsError("repair_required")
        resources, identity = self._resource_fact(evidence)
        configuration = self._configuration(ops, snapshot, options)
        if configuration.identity is not None and evidence is None:
            raise OpsError("ownership_conflict")
        if installation is None and configuration.identity is not None:
            prepared_configuration = pending.get("configuration") if pending else None
            if not isinstance(prepared_configuration, dict) or configuration.identity != replace(
                EnvironmentFileIdentity(**prepared_configuration["identity"]), path=str(self.environment)
            ):
                raise OpsError("ownership_conflict")
        database = classify_database(configuration, self.spec.server_version, for_repair=operation == "repair")
        current = installation is not None and resources == "verified"
        if operation == "install" and installation is not None and resources != "verified":
            raise OpsError("repair_required")
        registering = pending is not None and pending.get("stage") in {"registering", "registration_dispatching"}
        verify_running = registering and snapshot["manager"] == "active" and snapshot["maintenance"] is None
        maintenance = snapshot["maintenance"]
        preserve_stopped = (
            definition is not None
            and isinstance(maintenance, dict)
            and maintenance.get("phase") in {"manual_stopped", "start_failed"}
        )
        if operation == "install" and not current and not preserve_stopped and not verify_running:
            if database.classification == "new":
                database = admit_database(
                    configuration,
                    self.spec.server_version,
                    python_executable=self.directory / self.spec.python_console_relative,
                )
                require_admitted(database)
            elif resources == "verified" and database.classification == "existing_unknown":
                database = self.admit_start(ops, snapshot)
        may_start = (
            operation == "install"
            and not current
            and not preserve_stopped
            and (database.allowed_to_start or verify_running)
        )
        if (
            operation == "install"
            and not current
            and not preserve_stopped
            and not database.allowed_to_start
            and not verify_running
        ):
            raise OpsError(
                "database_incompatible"
                if database.classification == "incompatible"
                else "database_compatibility_unknown"
            )
        if definition is None:
            endpoint = urlsplit(configuration.endpoint)
            with socket.socket() as listener:
                try:
                    listener.bind((endpoint.hostname or "127.0.0.1", endpoint.port or 8000))
                except OSError as error:
                    raise OpsError("port_conflict") from error
        required = (
            0
            if resources == "verified"
            else self.spec.archive_size + self.spec.unpacked_size * (2 if operation == "repair" else 1)
        )
        for root in (self.layout.programs, self.layout.configuration, self.layout.state, self.layout.cache):
            if root.exists() or root.is_symlink():
                protect_directory(root)
            ancestor = root
            while not ancestor.exists():
                ancestor = ancestor.parent
            if not os.access(ancestor, os.W_OK) or shutil.disk_usage(ancestor).free < required:
                raise OpsError("insufficient_space_or_permissions")
        login = options.get(
            "start_on_login", definition.get("start_on_login", True) if definition else sys.platform != "win32"
        )
        if login is False and sys.platform != "win32":
            raise OpsError("option_unsupported")
        facts = {
            "catalog_sha256": self.catalog.sha256,
            "resource": self.spec.as_dict(),
            "layout": self.layout.as_dict(),
            "installation_id": evidence.get("installation_id") if evidence else None,
            "classification": "owned_current"
            if current
            else "owned_repairable"
            if installation
            else "pending_operation"
            if pending
            else "missing",
            "resources": resources,
            "resource_identity": identity,
            "configuration": configuration.as_dict(),
            "database": database.as_dict(),
            "service_before": protocol.digest(snapshot),
            "space_required": required,
            "verify_running": verify_running,
            "preserve_stopped": preserve_stopped,
            "repair_owner": repair_owner,
            "registration_present": definition is not None,
            "registration_removal": snapshot["registration_removal"],
        }
        plan = {
            "protocol_version": 1,
            "target_id": ops.target.target_id,
            "binding_id": protocol.digest(ops.target.as_dict()),
            "operation": operation,
            "options": options,
            "before": protocol.digest(facts),
            "effects": {"may_start": may_start, "preserves_data": True},
            "target_summary": {
                "python_executable": ops.target.python_executable,
                "package_version": ops.target.package_version,
                "env_file": str(configuration.identity.path if configuration.identity else self.environment),
                "endpoint": configuration.endpoint,
                "data_dir": str(configuration.data_dir),
                "start_on_login": login,
            },
            "installation": facts,
        }
        return {**plan, "plan_id": protocol.digest(plan)}

    def begin(
        self, ops: Ops, previous: dict[str, Any] | None, plan: dict[str, Any], operation_id: str
    ) -> dict[str, Any]:
        if previous is not None and previous.get("installation") is not None:
            return dict(previous["installation"])
        pending = self._pending(ops)
        if pending is not None and pending["installation_id"] == plan["installation"]["installation_id"]:
            return {
                **pending,
                "repair_owner": plan["installation"]["repair_owner"]
                or (operation_id if plan["operation"] == "repair" else None),
            }
        return {
            "installation_id": plan["installation"]["installation_id"] or str(uuid4()),
            "stage": "accepted",
            "prepared": None,
            "configuration": None,
            "resource_identity": plan["installation"]["resource_identity"],
            "backup_identity": None,
            "repair_owner": plan["installation"]["repair_owner"]
            or (operation_id if plan["operation"] == "repair" else None),
        }

    def execute(self, ops: Ops, record: dict[str, Any], emit: Callable[[dict[str, Any]], None]) -> dict[str, Any]:  # noqa: C901
        current = record["attempts"][-1]
        plan = current["plan"]
        state = current["installation"]
        cancelled = lambda: ops.store.cancelled(record["operation_id"], current["attempt"])
        checked_at = 0.0
        cancellation_seen = False

        def resource_cancelled() -> bool:
            nonlocal checked_at, cancellation_seen
            now = time.monotonic()
            if now - checked_at >= 0.5:
                cancellation_seen = cancelled()
                checked_at = time.monotonic()
            return cancellation_seen

        def checkpoint(stage: str) -> None:
            state["stage"] = stage
            current["phase"] = "executing"
            ops.store.save(record)
            emit({
                "type": "progress",
                "operation_id": record["operation_id"],
                "attempt": current["attempt"],
                "sequence": {
                    "preparing": 1,
                    "prepared": 2,
                    "publishing": 3,
                    "resources_published": 4,
                    "configuration_prepared": 5,
                    "configured": 6,
                    "registering": 7,
                    "complete": 8,
                }[stage],
                "phase": stage,
            })

        if cancelled():
            return {"outcome": "cancelled", "components": self.components(ops, state)}
        if plan["operation"] == "install" and plan["installation"]["classification"] == "owned_current":
            return {"outcome": "succeeded", "components": self.components(ops, state)}
        ops.store.write("_installation_owner.json", {"operation_id": record["operation_id"]})
        resource_state, _identity = self._resource_fact(state, cancelled=resource_cancelled)
        if resource_state != "verified":
            prepared = state.get("prepared")
            if prepared is not None and (
                Path(prepared["directory"]).exists() or Path(prepared["directory"]).is_symlink()
            ):
                try:
                    self._verify_prepared(prepared, cancelled=resource_cancelled)
                except ResourceError as error:
                    if error.code != "resource_corrupt":
                        raise
                    path = Path(prepared["directory"])
                    # Only a private, link-free tree with the recorded identity
                    # directly inside our staging root is disposable.
                    if (
                        not path.is_absolute()
                        or path.resolve().parent != (self.layout.programs / "staging").resolve()
                        or validate_owned_tree(path) != prepared["identity"]
                    ):
                        raise OpsError("resource_identity_changed") from error
                    shutil.rmtree(path)
                    prepared = None
            if prepared is None or not Path(prepared["directory"]).exists():
                state["prepared"] = None
                checkpoint("preparing")
                staging = self.layout.programs / "staging"
                try:
                    result = self.resources.prepare(self.spec, staging, cancelled=resource_cancelled)
                except ResourceError as error:
                    if error.code == "resource_cancelled":
                        return {"outcome": "cancelled", "components": self.components(ops, state)}
                    raise
                state["prepared"] = {"directory": str(result.directory), "identity": result.identity}
                checkpoint("prepared")
        if cancelled():
            return {"outcome": "cancelled", "components": self.components(ops, state)}
        with ops.controller.mutation(timeout=0):
            self._revalidate(ops, plan)
            if cancelled():
                return {"outcome": "cancelled", "components": self.components(ops, state)}
            if plan["operation"] == "repair":
                snapshot: dict[str, Any] = ops.controller.operation_snapshot()
                definition = ServiceDefinition.from_dict(snapshot["registration"]["definition"])
                with (
                    observe_native_commands(lambda event: self._native_checkpoint(ops, record, event)),
                    ops.controller.runtime_repair(expected_definition=definition, repair_id=state["repair_owner"]),
                ):
                    self._publish(state, record["operation_id"], checkpoint, cancelled=resource_cancelled)
                self._commit(ops, state)
            else:
                self._publish(state, record["operation_id"], checkpoint, cancelled=resource_cancelled)
                self._configure(state, plan, record, checkpoint)
                if cancelled():
                    return {"outcome": "cancelled", "components": self.components(ops, state)}
                checkpoint("registering")
                if cancelled():
                    return {"outcome": "cancelled", "components": self.components(ops, state)}
                if plan["effects"]["may_start"] and not plan["installation"]["verify_running"]:
                    configuration = self._configuration(ops, ops.controller.operation_snapshot(), plan["options"])
                    database = (
                        self.admit_start(ops, ops.controller.operation_snapshot())
                        if plan["installation"]["database"]["classification"] == "existing_ready"
                        else admit_database(
                            configuration,
                            self.spec.server_version,
                            python_executable=self.directory / self.spec.python_console_relative,
                        )
                    )
                    if database.as_dict() != plan["installation"]["database"]:
                        raise OpsError("plan_stale")
                with observe_native_commands(lambda event: self._native_checkpoint(ops, record, event)):
                    if plan["installation"]["preserve_stopped"]:
                        ops.controller.verify_stopped()
                    elif plan["installation"]["verify_running"]:
                        ops.controller.verify_running()
                    else:
                        state["stage"] = "registration_dispatching"
                        ops.store.save(record)
                        if cancelled():
                            state["stage"] = "registering"
                            return {"outcome": "cancelled", "components": self.components(ops, state)}
                        ops.controller.install(
                            env_file=self.environment,
                            start_on_login=plan["target_summary"]["start_on_login"],
                            require_ready=True,
                        )
                self._commit(ops, state)
            checkpoint("complete")
        components = self.components(ops, state)
        if plan["operation"] == "install" and plan["effects"]["may_start"]:
            components["readiness"] = "verified_during_operation"
        return {"outcome": "succeeded", "components": components}

    def _native_checkpoint(self, ops: Ops, record: dict[str, Any], event: dict[str, object]) -> None:
        record["attempts"][-1]["native_command"] = event
        ops.store.save(record)

    def _revalidate(self, ops: Ops, plan: dict[str, Any]) -> None:
        snapshot: dict[str, Any] = ops.controller.operation_snapshot()
        if protocol.digest(snapshot) != plan["installation"]["service_before"]:
            raise OpsError("plan_stale")
        summary = self._configuration(ops, snapshot, plan["options"])
        database = (
            self.admit_start(ops, snapshot)
            if plan["installation"]["database"]["classification"] == "existing_ready"
            else admit_database(
                summary,
                self.spec.server_version,
                python_executable=self.directory / self.spec.python_console_relative,
            )
            if plan["installation"]["database"]["classification"] == "new" and plan["operation"] == "install"
            else classify_database(summary, self.spec.server_version, for_repair=plan["operation"] == "repair")
        )
        if (
            summary.as_dict() != plan["installation"]["configuration"]
            or database.as_dict() != plan["installation"]["database"]
        ):
            raise OpsError("plan_stale")

    def _verify_prepared(self, prepared: dict[str, Any], *, cancelled: Callable[[], bool] = lambda: False) -> Path:
        path = Path(prepared["directory"])
        if (
            not path.is_relative_to(self.layout.programs / "staging")
            or validate_owned_tree(path) != prepared["identity"]
        ):
            raise OpsError("resource_identity_changed")
        verify(self.spec, path, cancelled=cancelled)
        return path

    @staticmethod
    def _publish_directory(source: Path, target: Path) -> None:
        os.replace(source, target)
        if sys.platform != "win32":
            for parent in {source.parent, target.parent}:
                descriptor = os.open(parent, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)

    def _publish(
        self,
        state: dict[str, Any],
        operation_id: str,
        checkpoint: Callable[[str], None],
        *,
        cancelled: Callable[[], bool],
    ) -> None:
        health, identity = self._resource_fact(state, cancelled=cancelled)
        if health == "verified":
            state["resource_identity"] = identity
            return
        prepared = state["prepared"]
        source = self._verify_prepared(prepared, cancelled=cancelled)
        parent = protect_directory(self.directory.parent, create=True)
        # Stay within the already-approved staging path budget even for a
        # maximum-length operation ID. Recovery derives the same owned path.
        backup = parent / f".repair-{protocol.digest(operation_id)[:32]}"
        if health == "damaged":
            if backup.exists() or backup.is_symlink():
                raise OpsError("resource_identity_changed")
            state["backup_identity"] = identity
            checkpoint("publishing")
            self._publish_directory(self.directory, backup)
        else:
            if backup.exists() and validate_owned_tree(backup) != state["backup_identity"]:
                raise OpsError("resource_identity_changed")
            checkpoint("publishing")
        if self.directory.exists() or self.directory.is_symlink():
            raise OpsError("resource_identity_changed")
        self._publish_directory(source, self.directory)
        verify(self.spec, self.directory, cancelled=cancelled)
        if validate_owned_tree(self.directory) != prepared["identity"]:
            raise OpsError("resource_identity_changed")
        state["resource_identity"] = prepared["identity"]
        checkpoint("resources_published")

    def _configure(
        self, state: dict[str, Any], plan: dict[str, Any], record: dict[str, Any], checkpoint: Callable[[str], None]
    ) -> None:
        if self.environment.exists():
            expected = state.get("configuration")
            if expected is None or read_protected_file(self.environment).identity != replace(
                EnvironmentFileIdentity(**expected["identity"]), path=str(self.environment)
            ):
                raise OpsError("configuration_conflict")
            return
        prepared: dict[str, Any] | None = state.get("configuration")
        if prepared is None:
            protect_directory(self.layout.configuration, create=True)
            path = (
                self.layout.configuration / f"pending-{record['operation_id']}-{record['attempts'][-1]['attempt']}.env"
            )
            if path.exists() or path.is_symlink():
                raise OpsError("configuration_conflict")
            identity = write_protected_file(
                path, new_configuration(self.layout.data, urlsplit(plan["target_summary"]["endpoint"]).port or 8000)
            )
            state["configuration"] = {"directory": str(path), "identity": asdict(identity)}
            checkpoint("configuration_prepared")
            prepared = cast(dict[str, Any], state["configuration"])
        path = Path(prepared["directory"])
        if path.parent != self.layout.configuration or read_protected_file(path).identity != EnvironmentFileIdentity(
            **prepared["identity"]
        ):
            raise OpsError("configuration_conflict")
        self._publish_directory(path, self.environment)
        checkpoint("configured")

    def _commit(self, ops: Ops, state: dict[str, Any]) -> None:
        snapshot: dict[str, Any] = ops.controller.operation_snapshot()
        definition = snapshot["registration"]["definition"]
        if definition is None:
            raise OpsError("ownership_conflict")
        ops.store.write(
            "_installation.json",
            {
                "installation_id": state["installation_id"],
                "binding_id": protocol.digest(ops.target.as_dict()),
                "resource_digest": self.spec.digest,
                "resource_identity": state["resource_identity"],
                "configuration_identity": definition["env_file"],
                "definition": definition,
            },
        )

    def inspect(self, ops: Ops, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Expose identities and database classification without hashing program files."""
        installation = self._installation(ops)
        evidence = installation or self._pending(ops)
        configuration = self._configuration(ops, snapshot, {})
        return {
            "installation_id": evidence["installation_id"] if evidence else None,
            "resource_id": self.spec.resource_id,
            "resource_digest": self.spec.digest,
            "endpoint": configuration.endpoint if configuration.identity is not None else None,
            "database": classify_database(configuration, self.spec.server_version).as_dict(),
            "connection_compatibility": "not_checked",
        }

    def components(self, ops: Ops, state: dict[str, Any] | None = None) -> dict[str, Any]:
        installation = self._installation(ops)
        evidence = state or installation or self._pending(ops)
        return {
            **ops.inspect(),
            "installation_id": evidence["installation_id"] if evidence else None,
            "resource_id": self.spec.resource_id,
            "resource_digest": self.spec.digest,
            "endpoint": ops.controller.status().endpoint,
            "connection_compatibility": "not_checked",
            "installation_stage": state["stage"] if state else None,
        }
