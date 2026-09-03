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

"""Windows Task Scheduler adapter for the personal PowerContext Server."""

from __future__ import annotations

import csv
import io
import json
import os
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path

from powercontext.service.adapters.base import (
    atomic_write,
    decode_metadata,
    encode_metadata,
    inspect_artifact,
    service_python_executable,
)
from powercontext.service.model import (
    DEFINITION_VERSION,
    OWNERSHIP_MARKER,
    ManagerOwnershipState,
    ManagerRegistration,
    ManagerState,
    NativeRegistration,
    RegistrationState,
    ServiceDefinition,
    ServiceError,
    SupportState,
)

_TASK_NAMESPACE = "http://schemas.microsoft.com/windows/2004/02/mit/task"
_TASK_IDENTIFIER = r"\PowerContext Personal Server"
_TASK_ARTIFACT_NAME = "personal-server.xml"
_METADATA_PREFIX = "X-PowerContext-Metadata: "
_DESCRIPTION = "Managed by PowerContext."
_RESTART_INTERVAL = "PT1M"
_RESTART_COUNT = "3"
_COMMAND_TIMEOUT_SECONDS = 30
_TASK_TIMEOUT_SECONDS = 10.0
_MAX_TASK_XML_BYTES = 1024 * 1024
_TASK_NOT_FOUND_HRESULT = 0x80070002
_TASK_HAS_NOT_RUN_RESULT = 0x41303
_TASK_STATUS_RESULT_RANGE = range(0x41300, 0x41400)
_BUILTIN_SERVICE_SIDS = {"s-1-5-18", "s-1-5-19", "s-1-5-20"}
_BUILTIN_SERVICE_ACCOUNTS = {"system", "local service", "network service"}


class WindowsTaskSchedulerAdapter:
    """Manage one current-user Task Scheduler task without administrator privileges."""

    identifier = _TASK_IDENTIFIER

    def __init__(
        self,
        *,
        home: Path | None = None,
        config_home: Path | None = None,
        identifier: str | None = None,
        user_account: str | None = None,
        user_sid: str | None = None,
    ) -> None:
        self.identifier = _normalize_identifier(identifier or type(self).identifier)
        profile_root = home or Path.home()
        root = (
            Path(config_home)
            if config_home is not None
            else Path(os.environ.get("LOCALAPPDATA", profile_root / "AppData" / "Local"))
        )
        self.artifact_path = root / "PowerContext" / "Services" / _TASK_ARTIFACT_NAME
        self.lock_path = self.artifact_path.with_name(".personal-server.lock")
        self._user_account_override = user_account
        self._user_sid_override = user_sid

    def platform_support(self) -> tuple[SupportState, str]:
        if sys.platform != "win32":
            return SupportState.UNSUPPORTED, "Task Scheduler personal services are available only on Windows"
        if shutil.which("schtasks.exe") is None:
            return SupportState.UNSUPPORTED, "schtasks.exe is not installed or is not on PATH"
        if shutil.which("powershell.exe") is None:
            return SupportState.UNSUPPORTED, "powershell.exe is not installed or is not on PATH"
        try:
            account, sid = self._user_identity()
        except ServiceError as error:
            return SupportState.UNSUPPORTED, str(error)
        try:
            service_python_executable()
        except ServiceError as error:
            return SupportState.UNSUPPORTED, str(error)
        if _is_service_account(account, sid):
            return SupportState.UNSUPPORTED, "personal services must run as the current interactive user"
        return SupportState.SUPPORTED, "Windows Task Scheduler is available"

    def support(self) -> tuple[SupportState, str]:
        support, detail = self.platform_support()
        if support is SupportState.UNSUPPORTED:
            return support, detail
        result = self._run("/Query", "/TN", self.identifier, "/XML", "/HRESULT", check=False)
        if result.returncode == 0 or _is_task_not_found(result):
            return SupportState.SUPPORTED, "the current user's Task Scheduler is available"
        return SupportState.UNSUPPORTED, "the current user's Task Scheduler is unavailable" + _command_detail(
            result.stderr
        )

    def inspect(self) -> NativeRegistration:
        state, content, detail = inspect_artifact(self.artifact_path)
        if state is not RegistrationState.INSTALLED or content is None:
            return NativeRegistration(state, content=content, detail=detail)
        try:
            root = _parse_xml(content)
            definition = _definition_from_task(root)
            expected = self.render(definition)
        except (ET.ParseError, TypeError, ValueError, ServiceError) as error:
            return NativeRegistration(RegistrationState.INVALID, content=content, detail=str(error))
        if (
            definition.ownership != OWNERSHIP_MARKER
            or definition.definition_version != DEFINITION_VERSION
            or expected != content
            or _text(_child(root, "RegistrationInfo"), "URI") != self.identifier
        ):
            return NativeRegistration(
                RegistrationState.INVALID,
                content=content,
                detail="the installed Task Scheduler definition does not match its PowerContext metadata",
            )
        return NativeRegistration(RegistrationState.INSTALLED, definition=definition, content=content)

    def loaded_registration(self) -> ManagerRegistration:
        result = self._run("/Query", "/TN", self.identifier, "/XML", "/HRESULT", check=False)
        if _is_task_not_found(result):
            return ManagerRegistration(ManagerOwnershipState.NOT_LOADED)
        if result.returncode != 0:
            return ManagerRegistration(
                ManagerOwnershipState.UNKNOWN,
                detail=f"cannot inspect loaded Task Scheduler task{_command_detail(result.stderr)}",
            )
        try:
            root = _parse_xml(result.stdout)
            definition = _definition_from_task(root)
        except (ET.ParseError, TypeError, ValueError) as error:
            return ManagerRegistration(ManagerOwnershipState.FOREIGN, detail=str(error))
        if definition.ownership != OWNERSHIP_MARKER or definition.definition_version != DEFINITION_VERSION:
            return ManagerRegistration(
                ManagerOwnershipState.FOREIGN,
                definition=definition,
                detail=f"loaded Task Scheduler task {self.identifier} has an unsupported PowerContext definition",
            )
        detail = self._task_mismatch(root, definition)
        if detail is not None:
            return ManagerRegistration(
                ManagerOwnershipState.FOREIGN,
                definition=definition,
                detail=f"loaded Task Scheduler task {self.identifier} does not match the PowerContext definition: {detail}",
            )
        return ManagerRegistration(ManagerOwnershipState.OWNED, definition=definition)

    def render(self, definition: ServiceDefinition) -> bytes:
        account, sid = self._user_identity()
        root = ET.Element(_tag("Task"), {"version": "1.3"})

        registration = ET.SubElement(root, _tag("RegistrationInfo"))
        ET.SubElement(registration, _tag("Author")).text = "PowerContext"
        ET.SubElement(
            registration, _tag("Description")
        ).text = f"{_DESCRIPTION}\n{_METADATA_PREFIX}{encode_metadata(definition)}"
        ET.SubElement(registration, _tag("URI")).text = self.identifier

        if definition.start_on_login:
            triggers = ET.SubElement(root, _tag("Triggers"))
            logon = ET.SubElement(triggers, _tag("LogonTrigger"))
            ET.SubElement(logon, _tag("Enabled")).text = "true"
            ET.SubElement(logon, _tag("UserId")).text = account

        principals = ET.SubElement(root, _tag("Principals"))
        principal = ET.SubElement(principals, _tag("Principal"), {"id": "Author"})
        ET.SubElement(principal, _tag("UserId")).text = sid
        ET.SubElement(principal, _tag("LogonType")).text = "InteractiveToken"
        ET.SubElement(principal, _tag("RunLevel")).text = "LeastPrivilege"

        settings = ET.SubElement(root, _tag("Settings"))
        for name, value in (
            ("MultipleInstancesPolicy", "IgnoreNew"),
            ("DisallowStartIfOnBatteries", "false"),
            ("StopIfGoingOnBatteries", "false"),
            ("AllowHardTerminate", "true"),
            ("StartWhenAvailable", "true"),
            ("Hidden", "true"),
            ("ExecutionTimeLimit", "PT0S"),
            ("UseUnifiedSchedulingEngine", "true"),
            ("Priority", "7"),
        ):
            ET.SubElement(settings, _tag(name)).text = value
        restart = ET.SubElement(settings, _tag("RestartOnFailure"))
        ET.SubElement(restart, _tag("Interval")).text = _RESTART_INTERVAL
        ET.SubElement(restart, _tag("Count")).text = _RESTART_COUNT

        actions = ET.SubElement(root, _tag("Actions"), {"Context": "Author"})
        execute = ET.SubElement(actions, _tag("Exec"))
        arguments = _launcher_arguments(definition)
        ET.SubElement(execute, _tag("Command")).text = arguments[0]
        ET.SubElement(execute, _tag("Arguments")).text = subprocess.list2cmdline(arguments[1:])
        ET.SubElement(execute, _tag("WorkingDirectory")).text = definition.data_dir

        ET.register_namespace("", _TASK_NAMESPACE)
        # ``schtasks /Create /XML`` requires the Windows XML encoding rather than a UTF-8 file.
        return ET.tostring(root, encoding="utf-16", xml_declaration=True)

    def write(self, content: bytes) -> None:
        definition = _definition_from_task(_parse_xml(content))
        (Path(definition.data_dir) / "logs").mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic_write(self.artifact_path, content)

    def restore(self, content: bytes | None) -> None:
        if content is None:
            self.artifact_path.unlink(missing_ok=True)
        else:
            atomic_write(self.artifact_path, content)

    def reload(self) -> None:
        return None

    def enable(self) -> None:
        registration = self.inspect()
        if registration.definition is None:
            raise ServiceError(registration.detail or "the Task Scheduler definition is not installed")
        _require_owned_or_not_loaded(self.loaded_registration())
        self._run(
            "/Create",
            "/TN",
            self.identifier,
            "/XML",
            str(self.artifact_path),
            "/F",
            "/HRESULT",
        )
        self._run("/Change", "/TN", self.identifier, "/ENABLE", "/HRESULT")

    def start(self, *, reload_definition: bool) -> None:
        loaded = self.loaded_registration()
        _require_owned_or_not_loaded(loaded)
        if loaded.state is ManagerOwnershipState.OWNED and reload_definition:
            state = self.manager_state()
            if state is ManagerState.UNKNOWN:
                raise ServiceError(  # noqa: TRY003
                    f"cannot determine whether Task Scheduler task {self.identifier} is running"
                )
            if state is ManagerState.ACTIVE:
                self._run("/End", "/TN", self.identifier, "/HRESULT")
                self._wait_for_inactive()
        self._run("/Run", "/TN", self.identifier, "/HRESULT")

    def stop(self) -> None:
        loaded = self.loaded_registration()
        _require_owned_or_not_loaded(loaded)
        if loaded.state is ManagerOwnershipState.NOT_LOADED:
            return
        state = self.manager_state()
        if state is ManagerState.UNKNOWN:
            raise ServiceError(  # noqa: TRY003
                f"cannot determine whether Task Scheduler task {self.identifier} is running"
            )
        if state is ManagerState.ACTIVE:
            self._run("/End", "/TN", self.identifier, "/HRESULT")
            self._wait_for_inactive()

    def disable(self) -> None:
        _require_owned_or_not_loaded(self.loaded_registration())
        if self.loaded_registration().state is ManagerOwnershipState.NOT_LOADED:
            return
        self._run("/Change", "/TN", self.identifier, "/DISABLE", "/HRESULT")

    def remove(self) -> None:
        loaded = self.loaded_registration()
        _require_owned_or_not_loaded(loaded)
        if loaded.state is not ManagerOwnershipState.NOT_LOADED:
            self._run("/Delete", "/TN", self.identifier, "/F", "/HRESULT")
        self.artifact_path.unlink(missing_ok=True)

    def manager_state(self) -> ManagerState:
        result = self._run_task_info(check=False)
        if result.returncode != 0:
            return ManagerState.UNKNOWN
        try:
            values = json.loads(result.stdout)
        except json.JSONDecodeError:
            return ManagerState.UNKNOWN
        if not isinstance(values, dict):
            return ManagerState.UNKNOWN
        status = values.get("State")
        if not isinstance(status, str):
            return ManagerState.UNKNOWN
        status = status.casefold()
        if status == "notfound":
            return ManagerState.INACTIVE
        if status == "running":
            return ManagerState.ACTIVE
        if status in {"ready", "disabled", "queued"}:
            last_result = _last_result(values.get("LastTaskResult"))
            return ManagerState.FAILED if last_result not in {None, 0} else ManagerState.INACTIVE
        return ManagerState.UNKNOWN

    def log_location(self, definition: ServiceDefinition | None) -> str | None:
        if definition is None:
            return None
        return str(Path(definition.data_dir) / "logs")

    def uninstall_recovery(self, stage: str) -> str:
        commands = {
            "stop": f'schtasks.exe /End /TN "{self.identifier}" /HRESULT',
            "disable": f'schtasks.exe /Change /TN "{self.identifier}" /DISABLE /HRESULT',
            "remove": f'schtasks.exe /Delete /TN "{self.identifier}" /F /HRESULT',
            "reload": f'schtasks.exe /Query /TN "{self.identifier}" /XML /HRESULT',
        }
        return commands.get(stage, f'schtasks.exe /Query /TN "{self.identifier}" /XML /HRESULT')

    def _user_identity(self) -> tuple[str, str]:
        account, sid = _current_user_identity() if sys.platform == "win32" else _test_user_identity()
        return self._user_account_override or account, self._user_sid_override or sid

    def _task_mismatch(self, root: ET.Element, definition: ServiceDefinition) -> str | None:
        account, sid = self._user_identity()
        expected_arguments = _launcher_arguments(definition)
        registration = _child(root, "RegistrationInfo")
        triggers = _child(root, "Triggers")
        logon = _child(triggers, "LogonTrigger") if triggers is not None else None
        principals = _child(root, "Principals")
        principal = _child(principals, "Principal") if principals is not None else None
        settings = _child(root, "Settings")
        actions = _child(root, "Actions")
        execute = _child(actions, "Exec") if actions is not None else None
        structure_mismatch = _task_structure_mismatch(root, definition)
        if structure_mismatch is not None:
            return structure_mismatch
        if definition.start_on_login:
            logon_matches = logon is not None and (
                _text(logon, "Enabled").casefold() in {"", "true"}
                and _text(logon, "UserId").casefold() in {account.casefold(), sid.casefold()}
            )
        else:
            logon_matches = logon is None
        checks = (
            (_text(registration, "URI") == self.identifier, "URI"),
            (logon_matches, "logon trigger"),
            (_text(principal, "UserId").casefold() in {account.casefold(), sid.casefold()}, "principal user"),
            (_text(principal, "LogonType") == "InteractiveToken", "logon type"),
            (_text(principal, "RunLevel") in {"", "LeastPrivilege"}, "run level"),
            (_text(settings, "MultipleInstancesPolicy") == "IgnoreNew", "multiple-instance policy"),
            (_text(settings, "DisallowStartIfOnBatteries").casefold() == "false", "battery start policy"),
            (_text(settings, "StopIfGoingOnBatteries").casefold() == "false", "battery stop policy"),
            (_text(settings, "StartWhenAvailable").casefold() == "true", "start-when-available policy"),
            (_text(settings, "Hidden").casefold() == "true", "hidden-window policy"),
            (_text(_child(settings, "RestartOnFailure"), "Interval") == _RESTART_INTERVAL, "restart interval"),
            (_text(_child(settings, "RestartOnFailure"), "Count") == _RESTART_COUNT, "restart count"),
            (_same_path(_text(execute, "Command"), expected_arguments[0]), "launcher executable"),
            (_text(execute, "Arguments") == subprocess.list2cmdline(expected_arguments[1:]), "launcher arguments"),
            (_same_path(_text(execute, "WorkingDirectory"), definition.data_dir), "working directory"),
        )
        for matches, name in checks:
            if not matches:
                return name
        return None

    def _wait_for_inactive(self) -> None:
        deadline = time.monotonic() + _TASK_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            state = self.manager_state()
            if state is ManagerState.INACTIVE:
                return
            if state is ManagerState.UNKNOWN:
                raise ServiceError(  # noqa: TRY003
                    f"cannot verify that Task Scheduler task {self.identifier} stopped"
                )
            time.sleep(0.05)
        raise ServiceError(f"Task Scheduler task {self.identifier} did not stop")  # noqa: TRY003

    def _run(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        command = ["schtasks.exe", *arguments]
        try:
            result = subprocess.run(  # noqa: S603
                command,
                capture_output=True,
                text=True,
                timeout=_COMMAND_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ServiceError(f"failed to execute schtasks.exe: {error}") from error  # noqa: TRY003
        if check and result.returncode != 0:
            detail = _command_detail(result.stderr or result.stdout)
            raise ServiceError(f"schtasks.exe {arguments[0]} failed{detail}")  # noqa: TRY003
        return result

    def _run_task_info(self, *, check: bool = True) -> subprocess.CompletedProcess[str]:
        task_path, task_name = _task_path_and_name(self.identifier)
        environment = os.environ.copy()
        environment["POWERCONTEXT_TASK_PATH"] = task_path
        environment["POWERCONTEXT_TASK_NAME"] = task_name
        script = (
            "$taskPath = $env:POWERCONTEXT_TASK_PATH; "
            "$taskName = $env:POWERCONTEXT_TASK_NAME; "
            "$task = Get-ScheduledTask -TaskPath $taskPath -TaskName $taskName -ErrorAction SilentlyContinue; "
            "if ($null -eq $task) { "
            "[pscustomobject]@{ State = 'NotFound'; LastTaskResult = 0 } | ConvertTo-Json -Compress; exit 0 "
            "}; "
            "$info = Get-ScheduledTaskInfo -TaskPath $taskPath -TaskName $taskName -ErrorAction SilentlyContinue; "
            "if ($null -eq $info) { [Console]::Error.WriteLine('Task Scheduler info unavailable'); exit 1 }; "
            "[pscustomobject]@{ State = [string]$task.State; LastTaskResult = [int64]$info.LastTaskResult } "
            "| ConvertTo-Json -Compress"
        )
        try:
            result = subprocess.run(  # noqa: S603
                ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],  # noqa: S607
                capture_output=True,
                text=True,
                timeout=_COMMAND_TIMEOUT_SECONDS,
                check=False,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ServiceError(f"failed to execute powershell.exe: {error}") from error  # noqa: TRY003
        if check and result.returncode != 0:
            detail = _command_detail(result.stderr or result.stdout)
            raise ServiceError(f"powershell.exe Task Scheduler state query failed{detail}")  # noqa: TRY003
        return result


def _launcher_arguments(definition: ServiceDefinition) -> list[str]:
    log_dir = Path(definition.data_dir) / "logs"
    return [
        *definition.launcher_arguments(),
        "--stdout",
        str(log_dir / "server.stdout.log"),
        "--stderr",
        str(log_dir / "server.stderr.log"),
    ]


def _definition_from_task(root: ET.Element) -> ServiceDefinition:
    registration = _child(root, "RegistrationInfo")
    description = _text(registration, "Description")
    metadata = next(
        (
            line.strip()[len(_METADATA_PREFIX) :]
            for line in description.splitlines()
            if line.strip().startswith(_METADATA_PREFIX)
        ),
        None,
    )
    if _DESCRIPTION not in description or metadata is None:
        raise ValueError("Task Scheduler task is missing the PowerContext ownership metadata")  # noqa: TRY003
    return decode_metadata(metadata)


def _parse_xml(content: bytes | str) -> ET.Element:
    size = len(content) if isinstance(content, bytes) else len(content.encode("utf-8"))
    if size > _MAX_TASK_XML_BYTES:
        raise ValueError("Task Scheduler definition is too large")  # noqa: TRY003
    root = ET.fromstring(content)  # noqa: S314 - Task Scheduler supplies a bounded local XML document.
    if _local_name(root.tag) != "Task":
        raise ValueError("Task Scheduler definition has an unexpected root element")  # noqa: TRY003
    return root


def _tag(name: str) -> str:
    return f"{{{_TASK_NAMESPACE}}}{name}"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(parent: ET.Element | None, name: str) -> ET.Element | None:
    if parent is None:
        return None
    return next((child for child in parent if _local_name(child.tag) == name), None)


def _has_exact_children(parent: ET.Element | None, expected: tuple[str, ...]) -> bool:
    return parent is not None and tuple(_local_name(child.tag) for child in parent) == expected


def _has_exact_attributes(parent: ET.Element | None, expected: dict[str, str]) -> bool:
    return parent is not None and parent.attrib == expected


def _has_children(
    parent: ET.Element | None,
    *,
    required: tuple[str, ...],
    optional: tuple[str, ...] = (),
) -> bool:
    if parent is None:
        return False
    names = tuple(_local_name(child.tag) for child in parent)
    return (
        all(names.count(name) == 1 for name in required)
        and all(names.count(name) <= 1 for name in optional)
        and all(name in required or name in optional for name in names)
    )


def _task_structure_mismatch(root: ET.Element, definition: ServiceDefinition) -> str | None:
    triggers = _child(root, "Triggers")
    principals = _child(root, "Principals")
    actions = _child(root, "Actions")
    return next(
        (
            mismatch
            for mismatch in (
                _action_structure_mismatch(actions),
                _principal_structure_mismatch(principals),
                _trigger_structure_mismatch(triggers, definition),
            )
            if mismatch is not None
        ),
        None,
    )


def _action_structure_mismatch(actions: ET.Element | None) -> str | None:
    execute = _child(actions, "Exec")
    if not _has_exact_attributes(actions, {"Context": "Author"}):
        return "action attributes"
    if not _has_exact_children(actions, ("Exec",)):
        return "action structure"
    if not _has_exact_attributes(execute, {}):
        return "exec action attributes"
    if not _has_exact_children(execute, ("Command", "Arguments", "WorkingDirectory")):
        return "exec action structure"
    return None


def _principal_structure_mismatch(principals: ET.Element | None) -> str | None:
    principal = _child(principals, "Principal")
    if not _has_exact_children(principals, ("Principal",)):
        return "principal structure"
    if not _has_exact_attributes(principal, {"id": "Author"}):
        return "principal attributes"
    if not _has_children(principal, required=("UserId", "LogonType"), optional=("RunLevel",)):
        return "principal element structure"
    return None


def _trigger_structure_mismatch(triggers: ET.Element | None, definition: ServiceDefinition) -> str | None:
    logon = _child(triggers, "LogonTrigger")
    if definition.start_on_login and not _has_exact_children(triggers, ("LogonTrigger",)):
        return "trigger structure"
    if definition.start_on_login and not _has_children(logon, required=("UserId",), optional=("Enabled",)):
        return "logon trigger structure"
    if not definition.start_on_login and triggers is not None and not _has_exact_children(triggers, ()):
        return "trigger structure"
    return None


def _text(parent: ET.Element | None, name: str) -> str:
    child = _child(parent, name)
    return "" if child is None or child.text is None else child.text.strip()


def _normalize_identifier(identifier: str) -> str:
    value = identifier.strip()
    if not value:
        raise ValueError("Task Scheduler task identifier must not be empty")  # noqa: TRY003
    if not value.startswith("\\"):
        value = f"\\{value}"
    if value.endswith("\\") or any(character in value for character in "\x00\r\n"):
        raise ValueError("Task Scheduler task identifier is invalid")  # noqa: TRY003
    return value


def _task_path_and_name(identifier: str) -> tuple[str, str]:
    parent, separator, name = identifier.rpartition("\\")
    if not separator or not name:
        raise ValueError("Task Scheduler task identifier is invalid")  # noqa: TRY003
    return (parent + "\\") if parent else "\\", name


def _same_path(actual: str, expected: str) -> bool:
    if not actual:
        return False
    return os.path.normcase(os.path.abspath(actual)) == os.path.normcase(os.path.abspath(expected))


def _is_service_account(account: str, sid: str) -> bool:
    return (
        sid.casefold() in _BUILTIN_SERVICE_SIDS or account.rsplit("\\", 1)[-1].casefold() in _BUILTIN_SERVICE_ACCOUNTS
    )


def _current_user_identity() -> tuple[str, str]:
    try:
        result = subprocess.run(
            ["whoami.exe", "/user", "/fo", "csv", "/nh"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ServiceError(f"cannot determine the current Windows user: {error}") from error  # noqa: TRY003
    if result.returncode != 0:
        raise ServiceError(f"cannot determine the current Windows user{_command_detail(result.stderr)}")  # noqa: TRY003
    for row in csv.reader(io.StringIO(result.stdout)):
        sid = next((value.strip() for value in reversed(row) if value.strip().upper().startswith("S-1-")), None)
        if sid is not None and row:
            return row[0].strip(), sid
    raise ServiceError("cannot determine the current Windows user SID")  # noqa: TRY003


def _test_user_identity() -> tuple[str, str]:
    account = os.environ.get("USERNAME") or os.environ.get("USER") or "current-user"
    return account, "S-1-5-21-0-0-0-1000"


def _is_task_not_found(result: subprocess.CompletedProcess[str]) -> bool:
    return result.returncode & 0xFFFFFFFF == _TASK_NOT_FOUND_HRESULT


def _last_result(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        result = value
    elif isinstance(value, str):
        try:
            result = int(value.strip(), 0)
        except ValueError:
            return None
    else:
        return None
    return None if result in _TASK_STATUS_RESULT_RANGE or result == _TASK_HAS_NOT_RUN_RESULT else result


def _command_detail(output: str) -> str:
    detail = " ".join(output.strip().splitlines())
    return f": {detail[:500]}" if detail else ""


def _require_owned_or_not_loaded(registration: ManagerRegistration) -> None:
    if registration.state in {ManagerOwnershipState.FOREIGN, ManagerOwnershipState.UNKNOWN}:
        raise ServiceError(registration.detail or "cannot verify the loaded Task Scheduler task ownership")


__all__: Sequence[str] = ["WindowsTaskSchedulerAdapter"]
