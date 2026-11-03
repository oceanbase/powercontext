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

"""Private atomic checkpoints; reads and cancellation never acquire the writer lock."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from filelock import FileLock, SoftFileLock, Timeout

from powercontext.ops.protocol import OpsError, canonical, decode, identifier, integer, plan
from powercontext.service.environment import (
    ensure_protected_file,
    protect_directory,
    read_protected_file,
    write_protected_file,
)


class OperationStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory).absolute()

    def read(self, name: str) -> dict[str, Any] | None:
        if not self.directory.exists():
            return None
        protect_directory(self.directory)
        path = self.directory / name
        if not path.exists():
            return None
        try:
            result = decode(read_protected_file(path).content)
        except (ValueError, OSError) as error:
            raise OpsError("record_invalid") from error
        if (
            not isinstance(result, dict)
            or type(result.get("format_version")) is not int
            or result.get("format_version") != 1
        ):
            raise OpsError("record_unsupported")
        return cast(dict[str, Any], result)

    def write(self, name: str, value: dict[str, Any]) -> None:
        protect_directory(self.directory, create=True)
        write_protected_file(self.directory / name, canonical({"format_version": 1, **value}))

    def operation(self, operation_id: str) -> dict[str, Any] | None:
        record = self.read(f"{identifier(operation_id)}.json")
        if record is None:
            return None
        attempts = record.get("attempts")
        if (
            set(record) != {"format_version", "operation_id", "binding_id", "attempts"}
            or record.get("operation_id") != operation_id
            or not isinstance(record.get("binding_id"), str)
            or not isinstance(attempts, list)
            or not 1 <= len(attempts) <= 64
        ):
            raise OpsError("record_invalid")
        for index, attempt in enumerate(attempts, 1):
            _validate_attempt(attempt, index)
        return record

    def save(self, record: dict[str, Any]) -> None:
        self.write(f"{identifier(record['operation_id'])}.json", record)

    def cancelled(self, operation_id: str, attempt: int) -> bool:
        return self.read(f"{identifier(operation_id)}.{integer(attempt)}.cancel.json") is not None

    def cancel(self, operation_id: str, attempt: int) -> None:
        self.write(f"{identifier(operation_id)}.{integer(attempt)}.cancel.json", {"requested": True})

    @contextmanager
    def mutation(self) -> Iterator[None]:
        protect_directory(self.directory, create=True)
        path = self.directory / "operations.lock"
        ensure_protected_file(path)
        lock = FileLock(str(path), timeout=0, mode=0o600)
        if isinstance(lock, SoftFileLock):
            raise OpsError("locking_unsupported")
        try:
            with lock:
                if isinstance(lock, SoftFileLock):
                    raise OpsError("locking_unsupported")
                yield
        except Timeout as error:
            raise OpsError("operation_busy") from error


def _validate_result(value: object) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or not set(value) <= {"outcome", "components", "error", "recovery_actions"}:
        raise OpsError("record_invalid")
    value = cast(dict[str, Any], value)
    if not isinstance(value.get("outcome"), str) or value.get("outcome") not in {
        "succeeded",
        "cancelled",
        "failed",
        "partial_failure",
        "uncertain",
    }:
        raise OpsError("record_invalid")
    if value.get("components") is not None and not isinstance(value["components"], dict):
        raise OpsError("record_invalid")
    error = value.get("error")
    if error is not None:
        if not isinstance(error, dict) or set(error) != {"code"}:
            raise OpsError("record_invalid")
        identifier(error.get("code"))
    recovery = value.get("recovery_actions")
    if recovery is not None and (
        not isinstance(recovery, list)
        or len(recovery) > 16
        or any(item not in ("inspect", "preflight") for item in recovery)
    ):
        raise OpsError("record_invalid")


def _validate_revision(value: object) -> None:
    if not isinstance(value, dict) or set(value) != {"path", "identity"}:
        raise OpsError("record_invalid")
    value = cast(dict[str, Any], value)
    if not isinstance(value["path"], str) or not isinstance(value["identity"], dict):
        raise OpsError("record_invalid")


def _validate_attempt(value: object, index: int) -> None:  # noqa: C901
    if not isinstance(value, dict):
        raise OpsError("record_invalid")
    attempt = cast(dict[str, Any], value)
    required = {"attempt", "plan", "phase", "result", "recovery"}
    if (
        not required
        <= set(attempt)
        <= required
        | {
            "configuration_revision",
            "native_command",
            "executor",
            "coordination_previous_operation_id",
            "coordination_previous_attempt",
            "installation",
        }
    ):
        raise OpsError("record_invalid")
    if type(attempt["attempt"]) is not int or attempt["attempt"] != index:
        raise OpsError("record_invalid")
    validated_plan = plan(attempt["plan"])
    if validated_plan["operation"] in {"install", "repair"} and "installation" not in attempt:
        raise OpsError("record_invalid")
    if not isinstance(attempt["phase"], str) or attempt["phase"] not in {"accepted", "executing", "finished"}:
        raise OpsError("record_invalid")
    _validate_result(attempt["result"])
    recovery = attempt["recovery"]
    if recovery is not None:
        if (
            not isinstance(recovery, dict)
            or not isinstance(recovery.get("action"), str)
            or recovery.get("action")
            not in {
                "completed",
                "verify_running",
                "configuration_switch",
            }
        ):
            raise OpsError("record_invalid")
        recovery = cast(dict[str, Any], recovery)
        if recovery["action"] == "configuration_switch":
            _validate_revision(recovery.get("revision"))
    # Early format-1 checkpoints without executor evidence remain readable;
    # callers report unknown rather than manufacture a process identity.
    for field, validate in (
        ("configuration_revision", _validate_revision),
        ("native_command", _validate_native),
        ("executor", _validate_executor),
        ("coordination_previous_operation_id", _validate_predecessor),
        ("coordination_previous_attempt", _validate_predecessor_attempt),
        ("installation", _validate_installation),
    ):
        if field in attempt:
            validate(attempt[field])


def _validate_installation(value: object) -> None:
    if not isinstance(value, dict):
        raise OpsError("record_invalid")
    state = cast(dict[str, Any], value)
    if set(state) != {
        "installation_id",
        "stage",
        "prepared",
        "configuration",
        "resource_identity",
        "backup_identity",
        "repair_owner",
    }:
        raise OpsError("record_invalid")
    identifier(state["installation_id"])
    if state["repair_owner"] is not None:
        identifier(state["repair_owner"])
    if state["stage"] not in (
        "accepted",
        "preparing",
        "prepared",
        "publishing",
        "resources_published",
        "configuration_prepared",
        "configured",
        "registering",
        "registration_dispatching",
        "complete",
    ):
        raise OpsError("record_invalid")
    for field in ("prepared", "configuration"):
        item = state[field]
        if item is not None and (
            not isinstance(item, dict)
            or set(item) != {"directory", "identity"}
            or not isinstance(item["directory"], str)
            or not isinstance(item["identity"], dict)
        ):
            raise OpsError("record_invalid")
    for field in ("resource_identity", "backup_identity"):
        item = state[field]
        if item is not None and (
            not isinstance(item, dict)
            or set(item) != {"device", "inode"}
            or any(type(part) is not int or part < 0 for part in item.values())
        ):
            raise OpsError("record_invalid")


def _validate_executor(value: object) -> None:
    if not isinstance(value, dict):
        raise OpsError("record_invalid")
    executor = cast(dict[str, Any], value)
    if set(executor) != {"pid"} or type(executor["pid"]) is not int or executor["pid"] <= 0:
        raise OpsError("record_invalid")


def _validate_predecessor(value: object) -> None:
    if value is not None:
        try:
            identifier(value)
        except OpsError as error:
            raise OpsError("record_invalid") from error


def _validate_predecessor_attempt(value: object) -> None:
    if value is not None and (type(value) is not int or value <= 0):
        raise OpsError("record_invalid")


def _validate_native(value: object) -> None:
    if not isinstance(value, dict):
        raise OpsError("record_invalid")
    native = cast(dict[str, Any], value)
    if (
        set(native) not in ({"phase"}, {"phase", "pid"})
        or not isinstance(native.get("phase"), str)
        or native.get("phase")
        not in {
            "dispatching",
            "running",
            "completed",
            "spawn_failed",
        }
    ):
        raise OpsError("record_invalid")
    if "pid" in native:
        integer(native["pid"])
    if native["phase"] == "running" and "pid" not in native:
        raise OpsError("record_invalid")
    if native["phase"] == "spawn_failed" and "pid" in native:
        raise OpsError("record_invalid")
