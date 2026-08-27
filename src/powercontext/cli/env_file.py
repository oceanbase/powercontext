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

"""Strict, shell-free loading for PowerContext environment files."""

from __future__ import annotations

import os
import re
from collections.abc import Collection, Iterator, Mapping, MutableMapping
from contextlib import contextmanager
from pathlib import Path

_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_EXPORT_PREFIX = re.compile(r"^export[ \t]+")
_NO_ESCAPED_CHARACTER = "No escaped character"
_NO_CLOSING_QUOTATION = "No closing quotation"
_UNSUPPORTED_EXPANSION = "shell expansion is not supported; quote or escape the value to keep it literal"


class EnvironmentFileError(ValueError):
    """Report an environment document that cannot be loaded safely."""


def parse_environment(content: str, *, source: str = "environment") -> dict[str, str]:
    """Parse simple shell-compatible assignments without evaluating shell code.

    A ``#`` only starts a comment at an unquoted word boundary, so values such as
    ``TOKEN=abc#123`` keep their full content:

        >>> parse_environment("TOKEN=abc#123\\nURL=https://example.com/#frag # comment\\n")
        {'TOKEN': 'abc#123', 'URL': 'https://example.com/#frag'}
    """

    environment: dict[str, str] = {}
    for line_number, line in enumerate(content.splitlines(), start=1):
        stripped = line.lstrip(" \t")
        if not stripped or stripped.startswith("#"):
            continue
        export = _EXPORT_PREFIX.match(stripped)
        if export is not None:
            stripped = stripped[export.end() :]
        if "\x00" in stripped:
            raise EnvironmentFileError(f"invalid NUL character at {source}:{line_number}")  # noqa: TRY003
        try:
            tokens = _split_shell_words(stripped)
        except ValueError as error:
            raise EnvironmentFileError(  # noqa: TRY003
                f"invalid assignment at {source}:{line_number}: {error}"
            ) from error
        if not tokens:
            continue
        if len(tokens) != 1 or "=" not in tokens[0]:
            raise EnvironmentFileError(f"invalid assignment at {source}:{line_number}")  # noqa: TRY003
        name, value = tokens[0].split("=", maxsplit=1)
        if _ENVIRONMENT_NAME.fullmatch(name) is None:
            raise EnvironmentFileError(  # noqa: TRY003
                f"invalid environment name at {source}:{line_number}: {name!r}"
            )
        if name in environment:
            raise EnvironmentFileError(  # noqa: TRY003
                f"duplicate environment name at {source}:{line_number}: {name}"
            )
        environment[name] = value
    return environment


def _split_shell_words(line: str) -> list[str]:  # noqa: C901
    """Split one shell assignment without expansion or command evaluation."""

    words: list[str] = []
    word: list[str] = []
    quote = ""
    word_started = False
    index = 0
    while index < len(line):
        character = line[index]
        if quote == "'":
            if character == quote:
                quote = ""
            else:
                word.append(character)
            word_started = True
        elif quote == '"':
            if character == quote:
                quote = ""
            elif character == "\\" and index + 1 < len(line) and line[index + 1] in {"$", "`", '"', "\\"}:
                index += 1
                word.append(line[index])
            elif character in {"$", "`"}:
                raise ValueError(_UNSUPPORTED_EXPANSION)
            else:
                word.append(character)
            word_started = True
        elif character in {"'", '"'}:
            quote = character
            word_started = True
        elif character == "\\":
            if index + 1 >= len(line):
                raise ValueError(_NO_ESCAPED_CHARACTER)
            index += 1
            word.append(line[index])
            word_started = True
        elif character in {" ", "\t"}:
            if word_started:
                words.append("".join(word))
                word = []
                word_started = False
        elif character == "#" and not word_started:
            break
        elif character in {"$", "`"} or (character == "~" and (not word or word[-1] in {"=", ":"})):
            raise ValueError(_UNSUPPORTED_EXPANSION)
        else:
            word.append(character)
            word_started = True
        index += 1
    if quote:
        raise ValueError(_NO_CLOSING_QUOTATION)
    if word_started:
        words.append("".join(word))
    return words


def read_environment_file(path: Path) -> dict[str, str]:
    """Read and parse one UTF-8 environment file."""

    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeError as error:
        raise EnvironmentFileError(f"invalid UTF-8 environment file: {path}") from error  # noqa: TRY003
    return parse_environment(content, source=str(path))


def apply_environment_file(
    path: Path,
    *,
    target: MutableMapping[str, str] | None = None,
    override: bool = False,
) -> Mapping[str, str]:
    """Load assignments into a process-like mapping, preserving existing values by default."""

    destination = os.environ if target is None else target
    loaded = read_environment_file(path)
    for name, value in loaded.items():
        if override or name not in destination:
            destination[name] = value
    return loaded


@contextmanager
def environment_file_context(path: Path, *, override: bool = False) -> Iterator[Mapping[str, str]]:
    """Apply a file for one process scope, then restore every affected value."""

    loaded = read_environment_file(path)
    with environment_context(loaded, override=override):
        yield loaded


@contextmanager
def environment_context(
    values: Mapping[str, str],
    *,
    override: bool = False,
    clear: Collection[str] = (),
) -> Iterator[None]:
    """Apply parsed values for one process scope, then restore every affected value."""

    loaded = dict(values)
    cleared = set(clear)
    affected = cleared | {name for name in loaded if override or name not in os.environ}
    original = {name: os.environ.get(name) for name in affected}
    try:
        for name in cleared:
            os.environ.pop(name, None)
        for name in affected:
            if name in loaded:
                os.environ[name] = loaded[name]
        yield
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


__all__ = [
    "EnvironmentFileError",
    "apply_environment_file",
    "environment_context",
    "environment_file_context",
    "parse_environment",
    "read_environment_file",
]
