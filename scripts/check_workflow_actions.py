#!/usr/bin/env python3
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

"""Require immutable commit pins for third-party GitHub Actions."""

from __future__ import annotations

import re
import sys
from collections import deque
from pathlib import Path

import yaml
from yaml.resolver import BaseResolver

COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
SUPPORTED_SUFFIXES = {".yaml", ".yml"}


class _UniqueKeyLoader(yaml.SafeLoader):
    """Reject duplicate mapping keys so validation matches one YAML meaning."""


class _DuplicateKeyError(yaml.YAMLError):
    def __init__(self) -> None:
        super().__init__("duplicate YAML key")


class _InvalidMappingKeyError(yaml.YAMLError):
    def __init__(self) -> None:
        super().__init__("unhashable YAML mapping key")


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            if key in mapping:
                raise _DuplicateKeyError()
            mapping[key] = loader.construct_object(value_node, deep=deep)
        except TypeError as error:
            raise _InvalidMappingKeyError() from error
    return mapping


_UniqueKeyLoader.add_constructor(BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


def _files_to_scan(arguments: list[str]) -> list[Path]:
    files: list[Path] = []
    for argument in arguments:
        path = Path(argument)
        if path.is_file():
            if path.suffix.lower() in SUPPORTED_SUFFIXES:
                files.append(path)
            continue
        if path.is_dir():
            files.extend(
                candidate
                for candidate in path.rglob("*")
                if candidate.is_file()
                and candidate.suffix.lower() in SUPPORTED_SUFFIXES
                and ".git" not in candidate.parts
            )
            continue
        print(f"path does not exist: {path}", file=sys.stderr)
        raise SystemExit(2)
    return sorted(set(files), key=lambda path: str(path).lower())


def _action_references(value: object) -> list[str]:
    references: list[str] = []
    pending = deque([value])
    visited: set[int] = set()
    while pending:
        current = pending.popleft()
        if not isinstance(current, (dict, list)):
            continue
        current_id = id(current)
        if current_id in visited:
            continue
        visited.add(current_id)
        if isinstance(current, dict):
            for key, child in current.items():
                if key == "uses":
                    references.append(child if isinstance(child, str) else "")
                else:
                    pending.append(child)
        else:
            pending.extend(current)
    return references


def _display_reference(reference: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_.@/-]{1,240}", reference):
        return reference
    return "[REDACTED]"


def main(arguments: list[str]) -> int:
    references_checked = 0
    violations: list[str] = []
    for path in _files_to_scan(arguments or [".github/workflows", ".github/actions"]):
        try:
            document = yaml.load(
                path.read_text(encoding="utf-8"),
                Loader=_UniqueKeyLoader,  # noqa: S506
            )
        except (OSError, UnicodeError, RecursionError, yaml.YAMLError):
            print(f"{path}: could not parse YAML", file=sys.stderr)
            return 1
        for reference in _action_references(document):
            references_checked += 1
            if reference.startswith("./") or reference.startswith("../"):
                continue
            if "@" not in reference or not COMMIT_SHA.fullmatch(reference.rsplit("@", 1)[1]):
                display_reference = _display_reference(reference)
                violations.append(f"{path}: uses reference '{display_reference}' must use a 40-character commit SHA")

    if violations:
        print("\n".join(violations), file=sys.stderr)
        return 1
    print(f"{references_checked} action references checked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
