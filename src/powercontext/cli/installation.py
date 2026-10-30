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

"""Compose native installation and connection configuration."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4


class SetupError(RuntimeError):
    """Report a failed installation or external setup command."""


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def write_json(path: Path, value: object) -> None:
    write_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def write_bytes(path: Path, value: bytes) -> None:
    """Replace one complete configuration file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def publish(replacements=(), writes=None, *, action: Callable[[], object] | None = None) -> None:  # noqa: C901
    """Publish local changes, restoring previous contents if writing or the action fails."""
    previous: list[tuple[Path, Path | None]] = []
    changes = [(target, source) for source, target in replacements] + list((writes or {}).items())
    for target, value in changes:
        if value is not None and not target.exists() and not target.is_symlink():
            backup = next(target.parent.glob(f".{target.name}.*.bak"), None)
            if backup is not None:
                message = f"Restore {target} from {backup} before retrying setup"
                raise SetupError(message)
    try:
        for target, value in changes:
            target.parent.mkdir(parents=True, exist_ok=True)
            backup = None
            if target.exists() or target.is_symlink():
                backup = target.with_name(f".{target.name}.{uuid4().hex}.bak")
                if value is None or (target.is_dir() and not target.is_symlink()):
                    os.replace(target, backup)
                else:
                    shutil.copy2(target, backup, follow_symlinks=False)
            previous.append((target, backup))
            if isinstance(value, Path):
                os.replace(value, target)
            elif value is not None:
                write_bytes(target, value)
        if action is not None:
            action()
    except BaseException as error:
        for target, backup in reversed(previous):
            try:
                if backup is None or (target.is_dir() and not target.is_symlink()):
                    _remove(target)
                if backup is not None:
                    os.replace(backup, target)
            except OSError:
                error.add_note(f"Could not restore {target}; previous contents remain at {backup}")
        raise
    for _, backup in previous:
        if backup is not None:
            _remove(backup)


@dataclass(frozen=True)
class PluginSource:
    location: str
    ref: str = "master"

    @contextmanager
    def package(self, host: str, *, linked: bool = False):
        from powercontext.cli.git_source import clone_github_source, is_local_source, resolve_plugin_source

        if linked or is_local_source(self.location):
            yield resolve_plugin_source(self.location, self.ref, host)
        else:
            with tempfile.TemporaryDirectory(prefix="powercontext-source-") as temporary:
                checkout = Path(temporary) / "checkout"
                clone_github_source(self.location, self.ref, checkout)
                yield resolve_plugin_source(str(checkout), self.ref, host)


def installers():
    from powercontext.cli.dsh import DshInstaller
    from powercontext.cli.hermes import HermesInstaller
    from powercontext.cli.kimi_code import KimiInstaller
    from powercontext.cli.minimax import MiniMaxInstaller
    from powercontext.cli.openclaw import OpenClawInstaller
    from powercontext.cli.opencode import OpenCodeInstaller
    from powercontext.cli.pi import PiInstaller
    from powercontext.cli.system import ClaudeInstaller, CodexInstaller
    from powercontext.cli.workbuddy import WorkBuddyInstaller
    from powercontext.cli.zcode import ZCodeInstaller

    return {
        "codex": CodexInstaller,
        "claude-code": ClaudeInstaller,
        "dsh": DshInstaller,
        "hermes": HermesInstaller,
        "kimi-code": KimiInstaller,
        "minimax": MiniMaxInstaller,
        "openclaw": OpenClawInstaller,
        "opencode": OpenCodeInstaller,
        "pi": PiInstaller,
        "workbuddy": WorkBuddyInstaller,
        "zcode": ZCodeInstaller,
    }


def setup(
    host: str,
    source: str,
    ref: str = "master",
    *,
    server_url: str | None = None,
    allow_insecure_http: bool | None = None,
    json_output: bool = False,
    **options: Any,
) -> dict[str, Any]:
    from powercontext.cli.authorization import validate_setup_authorization
    from powercontext.cli.transport import prepare_setup_transport

    try:
        validate_setup_authorization(host)
        installer = installers()[host](**options) if host == "minimax" else None
        native = {"native_endpoint": installer.native_endpoint()} if installer is not None else {}
        connection = prepare_setup_transport(
            host,
            server_url=server_url,
            allow_insecure_http=allow_insecure_http,
            json_output=json_output,
            dsh_target=options.get("dsh_target"),
            **native,
        )
        if installer is None:
            installer = installers()[host](**options)
        source_package = PluginSource(source, ref)
        report = (
            installer.install(source_package, connection=connection)
            if host == "minimax"
            else installer.install(source_package)
        )
        try:
            report.update(installer.configure(connection))
        except Exception as error:
            error.add_note("Plugin installation completed; connection configuration failed.")
            raise
        try:
            operation = getattr(installer, "enable", None)
            if operation is not None:
                operation()
        except Exception as error:
            error.add_note("Plugin installation and connection configuration completed; native activation failed.")
            raise
        return report  # noqa: TRY300
    except (OSError, ValueError) as error:
        failure = SetupError(f"Could not set up {host}: {error}")
        for note in getattr(error, "__notes__", ()):
            failure.add_note(note)
        raise failure from error
