# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Dependency-free semantic validation shared by code runtime and HTTP models."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any


def relative_path(value: str, *, allow_root: bool = False) -> str:
    """Validate a losslessly represented repository-relative path."""
    if allow_root and value == "":
        return value
    parts = value.split("/")
    if (
        not value
        or any(part in {"", ".", ".."} for part in parts)
        or "\\" in value
        or ":" in value
        or "\x00" in value
        or PurePosixPath(value).is_absolute()
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
    ):
        raise ValueError("invalid repository-relative path")  # noqa: TRY003
    return value


def validate_code_operation(operation: Mapping[str, Any]) -> None:
    """Reject code requests that cannot identify bounded repository evidence."""
    if "path_prefix" in operation:
        relative_path(operation["path_prefix"], allow_root=True)
    kind = operation["kind"]
    if kind in {"symbols", "explore"} and not operation["query"].strip():
        raise ValueError("query must not be blank")  # noqa: TRY003
    if kind in {"affected_tests", "impact_changes"}:
        paths = operation["paths"]
        for path in paths:
            relative_path(path)
        if len(set(paths)) != len(paths):
            raise ValueError("paths must be unique")  # noqa: TRY003
    if kind == "read":
        relative_path(operation["path"])
        if not 0 <= operation["end_line"] - operation["start_line"] < 200:
            raise ValueError("read range must contain 1 to 200 lines")  # noqa: TRY003


def validate_code_query(kind: str, expected_fingerprint: str | None, before_fingerprint: str | None) -> None:
    """Require the generation identities used by relational and source queries."""
    if (
        kind in {"callers", "callees", "impact", "affected_tests", "impact_changes", "read"}
        and not expected_fingerprint
    ):
        raise ValueError("expected_fingerprint is required for this operation")  # noqa: TRY003
    if kind == "impact_changes":
        if not before_fingerprint:
            raise ValueError("before_fingerprint is required for impact_changes")  # noqa: TRY003
    elif before_fingerprint is not None:
        raise ValueError("before_fingerprint is only valid for impact_changes")  # noqa: TRY003
