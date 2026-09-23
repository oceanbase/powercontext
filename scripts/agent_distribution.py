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

"""Minimal host profiles and deterministic assembly of evaluation packages."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path, PurePosixPath

from powercontext_integrations.resources import render_resources
from powercontext_integrations.targets import Target

ROOT = Path(__file__).resolve().parents[1]


def _check_handler(source: Path, handler: str) -> None:
    filename = handler.partition(":")[0]
    Target.relative_path(filename)
    path = source / filename
    if not path.is_file():
        message = f"missing adapter: {handler}"
        raise ValueError(message)


def require_runners() -> None:
    missing = [name for name in ("uvx", "npx", "powercontext-hook") if shutil.which(name) is None]
    if missing:
        message = (
            f"Missing required executables: {', '.join(missing)}. "
            "Install uv, Node.js, and the PowerContext client, then retry."
        )
        raise RuntimeError(message)


def _json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def _source_files(root: Path, source: str) -> list[Path]:
    # Respect Git's ignore rules; never package caches, credentials or local runtimes.
    output = subprocess.run(  # noqa: S603
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", source],  # noqa: S607
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    files = []
    for name in sorted(set(output.decode().split("\0")) - {""}):
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            message = f"source is not a contained regular file: {name}"
            raise ValueError(message)
        if path.is_file():
            files.append(path)
    return files


def assemble(target: Target, *, root: Path = ROOT) -> dict[str, bytes]:
    """Return a complete package without reading user configuration or installing anything."""

    for hook in target.hooks:
        _check_handler(root / target.source, hook.handler)
    files = {
        path.relative_to(root / target.source).as_posix(): path.read_bytes()
        for path in _source_files(root, target.source)
        if "tests" not in path.relative_to(root / target.source).parts
        and not path.name.endswith((".test.ts", ".spec.ts", ".test.tsx", ".spec.tsx"))
    }
    files.update({
        (PurePosixPath(target.resource_dir) / name).as_posix(): content
        for name, content in render_resources(target.target, root / target.source / target.resource_dir).items()
    })
    files.update(hook_files(target, root=root))
    files["distribution.json"] = _json({
        "schema_version": 1,
        "target": target.model_dump(mode="json"),
        "required_executables": ["uvx", "npx"] + (["powercontext-hook"] if target.language != "none" else []),
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())},
    })
    return files


def hook_files(target: Target, *, root: Path = ROOT) -> dict[str, bytes]:
    """Render the same native registrations for repository installs and assembled packages."""

    if target.hook_file is None:
        return {}
    hooks: dict[str, list[object]] = {}
    for hook in target.hooks:
        prefix = "${" + target.root_variable + "}"
        args = ["--script", f"{prefix}/{hook.handler}"]
        command: dict[str, object] = {"type": "command", "timeout": 10}
        if target.command_style == "argv":
            command.update(command="powercontext-hook", args=args)
        else:
            command["command"] = "powercontext-hook " + " ".join(f'"{arg}"' for arg in args)
        entry: dict[str, object] = {"hooks": [command]}
        if hook.matcher:
            entry["matcher"] = hook.matcher
        hooks.setdefault(hook.event, []).append(entry)
    files = {target.hook_file: _json({"hooks": hooks})}
    if target.hook_manifest:
        manifest = json.loads((root / target.source / target.hook_manifest).read_bytes())
        manifest["hooks"] = [target.hook_file]
        files[target.hook_manifest] = _json(manifest)
    return files


def _owned_files(destination: Path) -> dict[str, Path]:
    if destination.is_symlink() or any(path.is_symlink() for path in destination.rglob("*")):
        raise ValueError("output must not contain symlinks")  # noqa: TRY003
    receipt = destination / "distribution.json"
    old = json.loads(receipt.read_text())["files"] if receipt.is_file() else {}
    existing = {path.relative_to(destination).as_posix(): path for path in destination.rglob("*") if path.is_file()}
    for name, path in existing.items():
        if path.is_symlink() or not path.resolve().is_relative_to(destination.resolve()):
            message = f"output escapes package: {name}"
            raise ValueError(message)
        if name == "distribution.json":
            continue
        if name not in old or hashlib.sha256(path.read_bytes()).hexdigest() != old[name]:
            message = f"refusing to overwrite unowned or modified output: {path}"
            raise ValueError(message)
    return existing


def write_package(destination: Path, files: dict[str, bytes], *, check: bool = False) -> None:
    """Replace only owned, unmodified files; fail before writing on any conflict."""

    for name in files:
        Target.relative_path(name)
    existing = _owned_files(destination)
    if check:
        if set(existing) != set(files) or any(existing[name].read_bytes() != data for name, data in files.items()):
            message = f"distribution drift: {destination}"
            raise ValueError(message)
        return
    for name in existing.keys() - files.keys():
        existing[name].unlink()
    for name, data in files.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
