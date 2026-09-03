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

"""Protected environment-file loading for native personal services."""

from __future__ import annotations

import os
import stat
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from powercontext.cli.env_file import EnvironmentFileError, parse_environment
from powercontext.service.model import EnvironmentFileIdentity


class ProtectedEnvironmentFileError(EnvironmentFileError):
    """Report an environment file that is unsafe for a persistent user service."""


@dataclass(frozen=True)
class LoadedEnvironmentFile:
    path: Path
    identity: EnvironmentFileIdentity
    values: dict[str, str]


def load_protected_environment_file(
    path: Path,
    *,
    expected: EnvironmentFileIdentity | None = None,
) -> LoadedEnvironmentFile:
    """Open, validate, and parse one service env file without reopening its path."""

    candidate = Path(os.path.abspath(path.expanduser()))
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        if not hasattr(os, "O_NOFOLLOW") and candidate.is_symlink():
            raise ProtectedEnvironmentFileError(  # noqa: TRY003, TRY301
                "--env-file must not be a symbolic link"
            )
        descriptor = os.open(candidate, flags)
        before = os.fstat(descriptor)
        _validate_protection(candidate, before)
        identity = EnvironmentFileIdentity.from_stat(candidate, before)
        if expected is not None and identity != expected:
            raise ProtectedEnvironmentFileError(  # noqa: TRY003, TRY301
                f"--env-file changed since the personal service was installed: {candidate}"
            )
        content = _read_utf8(descriptor, candidate)
        after = os.fstat(descriptor)
        if EnvironmentFileIdentity.from_stat(candidate, after) != identity:
            raise ProtectedEnvironmentFileError(  # noqa: TRY003, TRY301
                f"--env-file changed while it was being read: {candidate}"
            )
    except ProtectedEnvironmentFileError:
        raise
    except OSError as error:
        raise ProtectedEnvironmentFileError(f"invalid --env-file {candidate}: {error}") from error  # noqa: TRY003
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
    try:
        values = parse_environment(content, source=str(candidate))
    except EnvironmentFileError as error:
        raise ProtectedEnvironmentFileError(str(error)) from error
    return LoadedEnvironmentFile(path=candidate, identity=identity, values=values)


def environment_identity_is_current(identity: EnvironmentFileIdentity) -> bool:
    """Return whether an installed env-file identity is still safe and unchanged."""

    try:
        load_protected_environment_file(Path(identity.path), expected=identity)
    except (OSError, EnvironmentFileError):
        return False
    return True


def _validate_protection(path: Path, status: os.stat_result) -> None:
    if not stat.S_ISREG(status.st_mode):
        raise ProtectedEnvironmentFileError(f"--env-file must be a regular file: {path}")  # noqa: TRY003
    if status.st_uid != os.getuid():
        raise ProtectedEnvironmentFileError(  # noqa: TRY003
            f"--env-file must be owned by the current user: {path}"
        )
    if status.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ProtectedEnvironmentFileError(  # noqa: TRY003
            f"--env-file must be accessible only by its owner; run `chmod 600 {path}`"
        )


def _read_utf8(descriptor: int, path: Path) -> str:
    chunks: list[bytes] = []
    while chunk := os.read(descriptor, 64 * 1024):
        chunks.append(chunk)
    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeError as error:
        raise ProtectedEnvironmentFileError(f"invalid UTF-8 environment file: {path}") from error  # noqa: TRY003


__all__ = [
    "LoadedEnvironmentFile",
    "ProtectedEnvironmentFileError",
    "environment_identity_is_current",
    "load_protected_environment_file",
]
