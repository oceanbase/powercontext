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

import ctypes
import os
import re
import stat
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
        owner_sid = _validate_protection(candidate, before)
        identity = EnvironmentFileIdentity.from_stat(candidate, before, owner_sid=owner_sid)
        if expected is not None and identity != expected:
            raise ProtectedEnvironmentFileError(  # noqa: TRY003, TRY301
                f"--env-file changed since the personal service was installed: {candidate}"
            )
        content = _read_utf8(descriptor, candidate)
        after = os.fstat(descriptor)
        after_owner_sid = _validate_protection(candidate, after)
        if EnvironmentFileIdentity.from_stat(candidate, after, owner_sid=after_owner_sid) != identity:
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


def _validate_protection(path: Path, status: os.stat_result) -> str | None:
    if not stat.S_ISREG(status.st_mode):
        raise ProtectedEnvironmentFileError(f"--env-file must be a regular file: {path}")  # noqa: TRY003
    if os.name == "nt":
        return _validate_windows_protection(path)
    if status.st_uid != os.getuid():
        raise ProtectedEnvironmentFileError(  # noqa: TRY003
            f"--env-file must be owned by the current user: {path}"
        )
    if status.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ProtectedEnvironmentFileError(  # noqa: TRY003
            f"--env-file must be accessible only by its owner; run `chmod 600 {path}`"
        )
    return None


def _validate_windows_protection(path: Path) -> str:
    """Require a Windows ACL limited to the interactive user and trusted OS admins."""

    account, sid = _windows_user_identity()
    owner_sid = _windows_file_owner_sid(path)
    if owner_sid.casefold() != sid.casefold():
        raise ProtectedEnvironmentFileError(  # noqa: TRY003
            f"--env-file must be owned by the current user (owner SID {sid}): {path}"
        )
    try:
        result = subprocess.run(  # noqa: S603
            ["icacls.exe", str(path)],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ProtectedEnvironmentFileError(f"cannot inspect the --env-file ACL: {error}") from error  # noqa: TRY003
    if result.returncode != 0:
        raise ProtectedEnvironmentFileError(  # noqa: TRY003
            f"cannot inspect the --env-file ACL: {' '.join(result.stderr.strip().splitlines())[:300]}"
        )

    allowed = {
        account.casefold(),
        sid.casefold(),
        "nt authority\\system",
        "builtin\\administrators",
        "owner rights",
    }
    principals: set[str] = set()
    for line in result.stdout.splitlines():
        entry = line.strip()
        match = re.search(r"(?P<rights>(?:\([^)]+\))+)$", entry)
        if match is None:
            continue
        prefix = entry[: match.start()].casefold()
        sid_match = re.search(r"s-1(?:-\d+)+", prefix, re.IGNORECASE)
        if sid_match is not None:
            principal = sid_match.group(0).casefold()
        else:
            principal = next(
                (
                    candidate
                    for candidate in (
                        account.casefold(),
                        "nt authority\\system",
                        "builtin\\administrators",
                        "owner rights",
                    )
                    if candidate in prefix
                ),
                "<unknown>",
            )
        principals.add(principal)
        if principal not in allowed:
            raise ProtectedEnvironmentFileError(  # noqa: TRY003
                "--env-file ACL grants access to an unexpected account; restrict it to the current user, "
                f"SYSTEM, and Administrators: {path}"
            )
    if not principals.intersection({account.casefold(), sid.casefold(), "owner rights"}):
        raise ProtectedEnvironmentFileError(  # noqa: TRY003
            f"--env-file ACL does not grant the current user access: {path}"
        )
    return owner_sid


def _windows_file_owner_sid(path: Path) -> str:
    """Read the file owner SID through the Windows security API."""

    owner = ctypes.c_void_p()
    security_descriptor = ctypes.c_void_p()
    local_free: Any = None
    try:
        win_dll = getattr(ctypes, "WinDLL")  # noqa: B009
        win_error = getattr(ctypes, "WinError")  # noqa: B009
        get_last_error = getattr(ctypes, "get_last_error")  # noqa: B009
        advapi32 = win_dll("Advapi32", use_last_error=True)
        kernel32 = win_dll("Kernel32", use_last_error=True)
        local_free = kernel32.LocalFree
        local_free.argtypes = [ctypes.c_void_p]
        local_free.restype = ctypes.c_void_p

        get_named_security_info = advapi32.GetNamedSecurityInfoW
        get_named_security_info.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        get_named_security_info.restype = ctypes.c_uint32
        error_code = get_named_security_info(
            str(path),
            1,  # SE_FILE_OBJECT
            0x00000001,  # OWNER_SECURITY_INFORMATION
            ctypes.byref(owner),
            None,
            None,
            None,
            ctypes.byref(security_descriptor),
        )
        if error_code:
            raise win_error(error_code)

        convert_sid = advapi32.ConvertSidToStringSidW
        convert_sid.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
        convert_sid.restype = ctypes.c_int
        owner_text = ctypes.c_wchar_p()
        if not convert_sid(owner, ctypes.byref(owner_text)) or owner_text.value is None:
            raise win_error(get_last_error())
        try:
            return owner_text.value
        finally:
            local_free(owner_text)
    except (AttributeError, OSError, TypeError, ValueError) as error:
        raise ProtectedEnvironmentFileError(f"cannot inspect the --env-file owner: {error}") from error  # noqa: TRY003
    finally:
        if security_descriptor.value and local_free is not None:
            local_free(security_descriptor)


def _windows_user_identity() -> tuple[str, str]:
    try:
        result = subprocess.run(
            ["whoami.exe", "/user", "/fo", "csv", "/nh"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ProtectedEnvironmentFileError(f"cannot determine the current Windows user: {error}") from error  # noqa: TRY003
    if result.returncode != 0:
        raise ProtectedEnvironmentFileError(  # noqa: TRY003
            f"cannot determine the current Windows user: {' '.join(result.stderr.strip().splitlines())[:300]}"
        )
    for line in result.stdout.splitlines():
        fields = [field.strip().strip('"') for field in line.split(",")]
        sid = next((field for field in reversed(fields) if field.upper().startswith("S-1-")), None)
        if sid is not None and fields:
            return fields[0], sid
    raise ProtectedEnvironmentFileError("cannot determine the current Windows user SID")  # noqa: TRY003


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
