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

"""Preserve environment documents and inspect existing SQLite metadata safely."""

from __future__ import annotations

import json
import re
import shlex
import sqlite3
import stat
from collections.abc import Iterator, Mapping
from contextlib import closing
from pathlib import Path

from powercontext.cli.env_file import EnvironmentFileError, _split_assignment, parse_environment

_BEGIN = "# >>> powercontext managed configuration >>>"
_END = "# <<< powercontext managed configuration <<<"
_LANGUAGE = "# powercontext-wizard-language="
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_PREFIX = re.compile(r"^[ \t]*(?:export[ \t]+)?")


def update_document(content: str, updates: Mapping[str, str | None], *, language: str) -> str:
    """Change only explicitly selected assignments, retaining unrelated bytes.

    Values are quoted as literal shell words. Neither the old document nor new
    values are evaluated, and duplicate assignments are rejected before editing.
    """

    if language not in {"en", "zh"}:
        raise EnvironmentFileError("unsupported wizard language")  # noqa: TRY003
    if any(_NAME.fullmatch(name) is None for name in updates):
        raise EnvironmentFileError("invalid environment name in wizard update")  # noqa: TRY003
    current = parse_environment(content)
    records = list(_records(content))
    _validate_metadata(records)
    newline = "\r\n" if "\r\n" in content else "\n"
    pending = {name: value for name, value in updates.items() if name in current and value != current[name]}
    additions = {name: value for name, value in updates.items() if name not in current}
    output, language_written = _apply_records(records, pending, additions, language, newline)
    if not content:
        output.extend((f"{_BEGIN}{newline}", f"# config-version=1{newline}"))
    appended = _new_assignments(additions, newline)
    if not language_written:
        appended.append(f"{_LANGUAGE}{language}{newline}")
    if appended and output and not output[-1].endswith(("\r", "\n")):
        output.append(newline)
    output.extend(appended)
    if not content:
        output.append(f"{_END}{newline}")
    result = "".join(output)
    parse_environment(result)
    return result


def _apply_records(
    records: list[tuple[str | None, str]],
    pending: dict[str, str | None],
    additions: dict[str, str | None],
    language: str,
    newline: str,
) -> tuple[list[str], bool]:
    output: list[str] = []
    language_written = False
    for name, raw in records:
        if name is not None:
            output.append(_replace_assignment(name, raw, pending))
        elif raw.strip().startswith(_LANGUAGE):
            if not language_written:
                output.append(f"{_LANGUAGE}{language}{newline}")
                language_written = True
        elif raw.strip() == _END:
            output.extend(_new_assignments(additions, newline))
            if not language_written:
                output.append(f"{_LANGUAGE}{language}{newline}")
                language_written = True
            output.append(raw)
        else:
            output.append(raw)
    return output, language_written


def _records(content: str) -> Iterator[tuple[str | None, str]]:
    """Use the loader's logical-line parser so quoted lines are never rewritten."""

    physical = content.splitlines(keepends=True)
    lines = iter(enumerate(physical))
    for index, raw in lines:
        stripped = raw.lstrip(" \t")
        if not stripped.strip() or stripped.startswith("#"):
            yield None, raw
            continue
        stripped = re.sub(r"^export[ \t]+", "", stripped)
        consumed = [raw]

        words = _split_assignment(
            stripped.rstrip("\r\n"), _consume_lines(lines, consumed), source="environment", line_number=index + 1
        )
        yield words[0].split("=", maxsplit=1)[0], "".join(consumed)


def _consume_lines(lines: Iterator[tuple[int, str]], consumed: list[str]) -> Iterator[tuple[int, str]]:
    for position, continuation in lines:
        consumed.append(continuation)
        yield position + 1, continuation.rstrip("\r\n")


def _validate_metadata(records: list[tuple[str | None, str]]) -> None:
    markers = [raw.strip() for name, raw in records if name is None and raw.strip() in {_BEGIN, _END}]
    if markers and markers != [_BEGIN, _END]:
        raise EnvironmentFileError("environment contains mismatched or repeated managed markers")  # noqa: TRY003
    for name, raw in records:
        if (
            name is None
            and raw.strip().startswith("# config-version=")
            and raw.strip().removeprefix("# config-version=").strip() != "1"
        ):
            raise EnvironmentFileError("unsupported PowerContext configuration version")  # noqa: TRY003


def _replace_assignment(name: str, raw: str, pending: dict[str, str | None]) -> str:
    if name not in pending:
        return raw
    value = pending.pop(name)
    trailer = _assignment_trailer(raw)
    if value is None:
        return trailer.lstrip(" \t") if "#" in trailer else ""
    prefix = _PREFIX.match(raw)
    return f"{prefix[0] if prefix else ''}{name}={shlex.quote(value)}{trailer}"


def _assignment_trailer(raw: str) -> str:
    quote = ""
    boundary = True
    index = 0
    while index < len(raw):
        character = raw[index]
        if quote:
            if character == quote:
                quote = ""
            elif quote == '"' and character == "\\":
                index += 1
        elif character in {"'", '"'}:
            quote = character
            boundary = False
        elif character == "\\":
            index += 1
            boundary = False
        elif character == "#" and boundary:
            start = index
            while start and raw[start - 1] in {" ", "\t"}:
                start -= 1
            return raw[start:]
        else:
            boundary = character in {" ", "\t", "\r", "\n"}
        index += 1
    return raw[len(raw.rstrip("\r\n")) :]


def _new_assignments(pending: dict[str, str | None], newline: str) -> list[str]:
    lines = [f"{name}={shlex.quote(value)}{newline}" for name, value in pending.items() if value is not None]
    pending.clear()
    return lines


def read_sqlite_summary(path: Path) -> dict[str, object]:
    """Read deployment metadata without creating a file or loading the runtime.

    This is a structural preview, not a compatibility or migration guarantee.
    Application data, arbitrary manifest fields, and credentials are excluded.
    """

    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return {"status": "new"}
    except OSError:
        return {"status": "unavailable", "reason": "cannot_access_file"}
    if not stat.S_ISREG(mode):
        return {"status": "unavailable", "reason": "not_regular_file"}
    try:
        uri = f"{path.resolve().as_uri()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=0.25)) as connection:
            connection.execute("PRAGMA query_only=ON")
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            known = {"pc_scopes", "pc_artifact_processing_schema", "pc_topic_memory_retrieval_shape"}
            summary: dict[str, object] = {
                "status": "existing" if tables & known else "unrecognized",
                "table_count": len(tables),
            }
            if "pc_scopes" in tables:
                summary["scope_count"] = connection.execute("SELECT COUNT(*) FROM pc_scopes").fetchone()[0]
            if "pc_artifact_processing_schema" in tables:
                _read_processing_manifest(connection, summary)
            if "pc_topic_memory_retrieval_shape" in tables:
                row = connection.execute("SELECT shape FROM pc_topic_memory_retrieval_shape LIMIT 1").fetchone()
                if row and row[0] in {"fts", "hybrid"}:
                    summary["topic_retrieval_shape"] = row[0]
            return summary
    except sqlite3.DatabaseError as error:
        code = getattr(error, "sqlite_errorcode", None)
        reason = "database_busy" if code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} else "database_unreadable"
        return {"status": "unavailable", "reason": reason}
    except OSError:
        return {"status": "unavailable", "reason": "cannot_access_file"}


def _read_processing_manifest(connection: sqlite3.Connection, summary: dict[str, object]) -> None:
    row = connection.execute("SELECT config_manifest FROM pc_artifact_processing_schema LIMIT 1").fetchone()
    if not row:
        return
    try:
        manifest = json.loads(row[0])
    except (TypeError, ValueError):
        summary["processing_manifest_invalid"] = True
        return
    if not isinstance(manifest, dict):
        summary["processing_manifest_invalid"] = True
        return
    safe: dict[str, object] = {}
    if isinstance(manifest.get("mode"), str) and manifest["mode"] in {"global", "dedicated"}:
        safe["mode"] = manifest["mode"]
    capabilities = manifest.get("capabilities")
    if isinstance(capabilities, list):
        safe["capabilities"] = [
            name
            for name in capabilities
            if isinstance(name, str) and name in {"memory", "topic-memory", "experience", "profile", "skill"}
        ]
    bindings = manifest.get("bindings")
    safe_bindings: dict[str, str] = {}
    if isinstance(bindings, dict):
        safe_bindings = {
            name: family
            for name, family in bindings.items()
            if isinstance(name, str)
            and re.fullmatch(r"[a-z0-9-]{1,128}", name)
            and isinstance(family, str)
            and family in {"memory", "topic-memory", "experience", "profile", "skill"}
        }
        safe["bindings"] = safe_bindings
    automatic = manifest.get("legacy_automatic_bindings")
    if isinstance(automatic, list):
        safe["legacy_automatic_bindings"] = [
            name for name in automatic if isinstance(name, str) and name in safe_bindings
        ]
    summary["processing_manifest"] = safe
