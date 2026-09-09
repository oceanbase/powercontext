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

"""Project the canonical Agent Plugin into the MiniMax submission format."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
PROFILE = Path("integrations/minimax/target.json")


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def json_object(content: bytes) -> dict[str, Any]:
    def unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")  # noqa: TRY003
            result[key] = value
        return result

    value = json.loads(content, object_pairs_hook=unique_keys)
    if not isinstance(value, dict):
        raise TypeError("Plugin JSON must be an object")  # noqa: TRY003
    return value


def read_source(root: Path, relative: str) -> bytes:
    path = root / relative
    if (
        Path(relative).is_absolute()
        or not path.resolve().is_relative_to(root.resolve())
        or ".." in Path(relative).parts
    ):
        raise ValueError(f"Source escapes repository: {relative}")  # noqa: TRY003
    current = root
    for part in Path(relative).parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"Symbolic link is not a portable source: {relative}")  # noqa: TRY003
    if not path.is_file() or path.stat().st_nlink != 1:
        raise ValueError(f"Source must be an ordinary unlinked file: {relative}")  # noqa: TRY003
    content = path.read_bytes()
    if content.startswith(b"version https://git-lfs.github.com/spec/v1"):
        raise ValueError(f"Git LFS pointer is not a package asset: {relative}")  # noqa: TRY003
    return content


def build_distribution(root: Path = ROOT) -> dict[str, bytes]:
    profile = json_object(read_source(root, PROFILE.as_posix()))
    if profile["schemaVersion"] != 1:
        raise ValueError("Unsupported MiniMax target profile")  # noqa: TRY003
    source = Path(profile["source"])
    portable = json_object(read_source(root, (source / "plugin.json").as_posix()))
    mcp = json_object(read_source(root, (source / "mcp.json").as_posix()))
    for server in mcp["mcpServers"].values():
        if set(server) - {"type", "url", "description", "timeout"}:
            raise ValueError("This target accepts only unauthenticated HTTP MCP configuration")  # noqa: TRY003
        url = urlsplit(server["url"])
        if (
            server["type"] not in {"streamable-http", "sse"}
            or url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
        ):
            raise ValueError("Unsupported MCP transport or credential-bearing URL")  # noqa: TRY003
    files = {destination: read_source(root, origin) for destination, origin in profile["files"].items()}
    for path in sorted((root / source / "skills").rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symbolic link in Skill: {path}")  # noqa: TRY003
        if not path.is_dir():
            destination = path.relative_to(root / source).as_posix()
            if destination in files:
                raise ValueError(f"Target file overrides canonical Skill: {destination}")  # noqa: TRY003
            files[destination] = read_source(root, path.relative_to(root).as_posix())
    skills = sorted(path for path in files if re.fullmatch(r"skills/[a-z0-9-]+/SKILL.md", path))
    if not skills:
        raise ValueError("The canonical plugin must supply at least one Skill")  # noqa: TRY003
    files["powercontext.mcp.json"] = json_bytes({"schemaVersion": 1, "mcpServers": mcp["mcpServers"]})
    files[".minimax-plugin/plugin.json"] = json_bytes({
        "schemaVersion": 1,
        "name": portable["name"],
        "displayName": profile["displayName"],
        "version": profile["version"],
        "description": portable["description"],
        "author": portable["author"]["name"],
        "icon": "icon.png",
        "category": profile["category"],
        "exampleQueries": profile["exampleQueries"],
        "apps": [],
        "mcpServers": ["powercontext.mcp.json"],
        "skills": skills,
    })
    validate_paths(files)
    return dict(sorted(files.items()))


def validate_paths(files: dict[str, bytes]) -> None:
    """Check the portable path and size budget of the generated package."""
    folded: set[str] = set()
    for name, content in files.items():
        parts = name.split("/")
        if (
            not re.fullmatch(r"[A-Za-z0-9._/-]+", name)
            or len(name) > 512
            or len(parts) > 16
            or any(part in {"", ".", ".."} or len(part) > 128 or part.endswith((".", " ")) for part in parts)
            or any(re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", part.split(".")[0]) for part in parts)
            or name.casefold() in folded
        ):
            raise ValueError(f"Invalid or conflicting package path: {name}")  # noqa: TRY003
        if len(content) > 16 * 1024 * 1024:
            raise ValueError(f"Package file exceeds 16 MiB: {name}")  # noqa: TRY003
        if name.endswith((".md", ".json")):
            if content.startswith(b"\xef\xbb\xbf"):
                raise ValueError(f"UTF-8 BOM in package file: {name}")  # noqa: TRY003
            content.decode("utf-8")
        if name.endswith(".json"):
            json_object(content)
        folded.add(name.casefold())
    if len(files) > 1024 or sum(map(len, files.values())) > 64 * 1024 * 1024:
        raise ValueError("Package exceeds the file count or total size budget")  # noqa: TRY003


def write_or_check(output: Path, files: dict[str, bytes], *, check: bool) -> None:
    if output.is_symlink():
        raise ValueError("Output directory must not be a symbolic link")  # noqa: TRY003
    existing: dict[str, bytes] = {}
    for path in sorted(output.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symbolic link in output: {path}")  # noqa: TRY003
        if path.is_dir():
            continue
        if not path.is_file() or path.stat().st_nlink != 1:
            raise ValueError(f"Output must contain only ordinary unlinked files: {path}")  # noqa: TRY003
        existing[path.relative_to(output).as_posix()] = path.read_bytes()
    stale = existing.keys() - files.keys()
    if stale:
        raise ValueError(f"Unexpected package files; review before removal: {sorted(stale)}")  # noqa: TRY003
    if check:
        if existing != files:
            raise ValueError("MiniMax package drifted; run make minimax-plugin")  # noqa: TRY003
        return
    for name, content in files.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(0o644)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify the package without writing files.")
    parser.add_argument("--output", type=Path, help="Use a separate package directory for local validation.")
    args = parser.parse_args()
    profile = json.loads((ROOT / PROFILE).read_text(encoding="utf-8"))
    try:
        write_or_check(args.output or ROOT / profile["output"], build_distribution(), check=args.check)
    except (TypeError, ValueError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
