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

"""Check installed roles, including the CLI's builtin dependency for offline archives, using Python -I."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path


async def _check_builtin(root: Path) -> None:
    from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
    from powercontext.builtin.persistence.sqlite import SQLiteConfig
    from powercontext.builtin.runtime import BuiltinConfig, open_builtin_runtime
    from powercontext.builtin.scope import ScopeDraft

    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{root / 'memory.db'}"))
    content = AtomicMemoryContent(kind="fact", text="Isolated installation preserves searchable memories.")
    async with open_builtin_runtime(config) as runtime:
        if runtime.scopes is None or runtime.atomic_memory is None:
            raise RuntimeError("Builtin runtime did not expose Scope and Atomic Memory services")  # noqa: TRY003
        scope = await runtime.scopes.create(
            ScopeDraft(
                title="Installation check",
                summary="Disposable smoke test",
                idempotency_key="isolated-installation",
            )
        )
        result = await runtime.atomic_memory.for_scope(scope.scope_id).create((content,))
        ref = result.primary.ref

    async with open_builtin_runtime(config) as runtime:
        if runtime.atomic_memory is None:
            raise RuntimeError("Reopened runtime did not expose Atomic Memory")  # noqa: TRY003
        memory = runtime.atomic_memory.for_scope(scope.scope_id)
        stored = await memory.get(ref.artifact_id)
        found = await memory.search("isolated installation", mode="text")
        if stored.ref != ref or stored.artifact.content.text != content.text:
            raise RuntimeError("Builtin Memory readback did not preserve the stored record")  # noqa: TRY003
        if [hit.text for hit in found.hits] != [content.text]:
            raise RuntimeError("Builtin Memory search did not return the stored record")  # noqa: TRY003


def _check_cli(root: Path, extras: set[str]) -> None:
    executable = Path(sys.executable).with_name("powercontext.exe" if os.name == "nt" else "powercontext")
    environment: dict[str, str] = {**os.environ, "POWERCONTEXT_HOME": str(root / "home"), "NO_COLOR": "1"}
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    commands = [(), ("config",), ("setup",), ("doctor",), ("skill", "remote-sync"), ("archive",)]
    if extras & {"builtin", "server"}:
        commands.append(("code",))
    if "server" in extras:
        commands.extend([("server",), ("service",)])
    for command in commands:
        result = subprocess.run(  # noqa: S603 - installed console script, fixed help commands
            [executable, *command, "--help"],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(f"CLI help failed for {command}: {result.stdout}{result.stderr}")  # noqa: TRY003
    unavailable = [] if "server" in extras else ["server", "service"]
    if not extras & {"builtin", "server"}:
        unavailable.append("code")
    for command in unavailable:
        result = subprocess.run(  # noqa: S603 - installed console script, fixed help commands
            [executable, command, "--help"],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 2 or f"No such command '{command}'" not in result.stdout + result.stderr:
            raise RuntimeError(f"CLI did not cleanly omit the unavailable {command} role: {result}")  # noqa: TRY003


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("extras", choices=("core", "builtin", "cli", "builtin,cli", "server,cli"))
    role = parser.parse_args().extras
    extras = set() if role == "core" else set(role.split(","))
    import powercontext

    if powercontext.PowerContext is None or powercontext.ArtifactRef is None:
        raise RuntimeError("Core public exports are unavailable")  # noqa: TRY003
    if not extras and importlib.util.find_spec("httpx") is not None:
        raise RuntimeError("Core installation unexpectedly includes httpx")  # noqa: TRY003
    if extras:
        from powercontext import artifact_plugins

        if powercontext.artifact_plugins is not artifact_plugins or artifact_plugins.ArtifactFamily is None:
            raise RuntimeError("Plugin public export is unavailable")  # noqa: TRY003
    if "cli" in extras:
        extras.add("builtin")
    with tempfile.TemporaryDirectory(prefix="powercontext-role-") as temporary:
        root = Path(temporary)
        if extras & {"builtin", "server"}:
            asyncio.run(_check_builtin(root))
        if "cli" in extras:
            _check_cli(root, extras)
    print(f"Verified isolated powercontext[{','.join(sorted(extras))}] installation.")


if __name__ == "__main__":
    main()
