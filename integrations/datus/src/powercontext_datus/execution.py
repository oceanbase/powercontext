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

"""Content identities for the launcher and every host-backed system mount.

The evaluator/host administrator is trusted. These are drift checks, not an
atomic filesystem snapshot or protection against a hostile host administrator.
"""

# ruff: noqa: TRY003, S603 - fixed, evaluator-owned launcher inspection.
from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from powercontext_datus.freeze import IntegrityError, digest_json

# Keep mounting and hashing driven by this one inventory. No broad /usr or /bin
# mount: neither shell tools nor mutable proc/sys hardware views are required by
# the certified native tool profile. Absent paths are identities too.
SYSTEM_MOUNTS = (
    "/usr/lib64",
    "/lib64",
    "/usr/lib/x86_64-linux-gnu",
    "/lib/x86_64-linux-gnu",
    "/usr/lib/aarch64-linux-gnu",
    "/lib/aarch64-linux-gnu",
    "/etc/ssl/certs",
    "/etc/resolv.conf",
    "/etc/nsswitch.conf",
    "/etc/hosts",
)
LOADER_INPUTS = ("/etc/ld.so.cache", "/etc/ld.so.preload", "/etc/ld.so.conf", "/etc/ld.so.conf.d")


def cpu_profile() -> str:
    # onnxruntime requires CPU feature discovery. Supply a frozen regular file,
    # not a live proc mount. Clock/calibration samples are not CPU capabilities;
    # omit them from BOTH the identity and the bytes visible to the worker.
    return (
        "\n".join(
            line
            for line in Path("/proc/cpuinfo").read_text().splitlines()
            if line.split(":", 1)[0].strip() not in {"cpu MHz", "bogomips", "BogoMIPS"}
        )
        + "\n"
    )


@contextmanager
def cpuinfo_file(profile: str) -> Iterator[int]:
    # An unlinked evaluator-owned file; only the read-only descriptor is passed
    # to bwrap, which copies it into a private read-only bind-data mount.
    with tempfile.TemporaryFile() as stream:
        stream.write(profile.encode())
        stream.flush()
        descriptor = os.open(f"/proc/self/fd/{stream.fileno()}", os.O_RDONLY | os.O_CLOEXEC)
        try:
            yield descriptor
        finally:
            os.close(descriptor)


def _stamp(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def file_digest(path: Path) -> str:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise IntegrityError("expected a regular execution file")
    with path.open("rb") as stream:
        if _stamp(os.fstat(stream.fileno())) != _stamp(before):
            raise IntegrityError("execution file changed before capture")
        result = hashlib.file_digest(stream, "sha256").hexdigest()
        if _stamp(os.fstat(stream.fileno())) != _stamp(before):
            raise IntegrityError("execution file changed during capture")
    if _stamp(path.lstat()) != _stamp(before):
        raise IntegrityError("execution file changed after capture")
    return result


def tree_digest(root: Path) -> str:
    """Hash all files, directory modes and link text, never silently skip entries.

    Internal links are not followed: workers resolve them in their mount
    namespace, where targets are either another hashed mount or absent.
    """
    inventory: dict[str, Any] = {}

    def visit(path: Path) -> None:
        before = path.lstat()
        entry: dict[str, Any] = {
            "mode": before.st_mode,
            "uid": before.st_uid,
            "gid": before.st_gid,
        }
        if stat.S_ISLNK(before.st_mode):
            entry["link"] = os.readlink(path)
        elif stat.S_ISREG(before.st_mode):
            entry["sha256"] = file_digest(path)
        elif stat.S_ISDIR(before.st_mode):
            for child in sorted(path.iterdir()):
                visit(child)
        else:
            raise IntegrityError(f"execution root contains a special file: {path}")
        if _stamp(path.lstat()) != _stamp(before):
            raise IntegrityError("execution root changed during capture")
        inventory[path.relative_to(root).as_posix()] = entry

    visit(root)
    return digest_json(inventory)


def path_identity(path: Path, cache: dict[Path, str]) -> dict[str, Any]:
    path = path.absolute()
    resolved = path.resolve(strict=True)
    # Per-capture deduplication only (e.g. merged-/usr). Never reuse old hashes.
    if resolved not in cache:
        cache[resolved] = tree_digest(resolved)
    return {"path": str(path), "resolved": str(resolved), "sha256": cache[resolved]}


def launcher_dependencies(binary: Path) -> list[Path]:
    """Inspect the native Linux launcher's complete dynamic-loader closure.

    ldd runs only after the expected launcher bytes and host roots have been
    verified by the caller. It is a host provisioning tool, not a worker input.
    No ambient LD_* variables reach either inspection or the actual launcher.
    """
    inspector = shutil.which("ldd", path=os.defpath)
    if inspector is None:
        raise IntegrityError("ldd is required to verify the native launcher dependencies")
    try:
        result = subprocess.run(
            [inspector, str(binary)],
            capture_output=True,
            text=True,
            env={"PATH": os.defpath, "LC_ALL": "C"},
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise IntegrityError("native launcher dependency inspection timed out") from error
    if result.returncode or "not found" in result.stdout:
        raise IntegrityError("cannot verify native bubblewrap dynamic dependencies")
    paths = set()
    for line in result.stdout.splitlines():
        value = line.strip().split(" => ", 1)[-1].split(" (", 1)[0]
        if value.startswith("/"):
            paths.add(Path(value))
        elif value and not value.startswith("linux-vdso"):
            raise IntegrityError("unrecognized native launcher dependency")
    if not paths:
        raise IntegrityError("native dynamic launcher dependency inventory is empty")
    return sorted(paths)


def execution_identity(expected: dict[str, Any] | None = None) -> dict[str, Any]:
    """Capture roots, rejecting known drift before inspecting/executing bwrap."""
    binary = shutil.which("bwrap")
    if binary is None:
        raise IntegrityError("bubblewrap unavailable; unsandboxed fallback is prohibited")
    cache: dict[Path, str] = {}
    launcher = path_identity(Path(binary), cache)
    launcher["content_sha256"] = file_digest(Path(launcher["resolved"]))
    if expected is not None and launcher != expected["launcher"]:
        raise IntegrityError("frozen bubblewrap path/content drifted")
    mounts = {}
    loader = {}
    for paths, identities in ((SYSTEM_MOUNTS, mounts), (LOADER_INPUTS, loader)):
        for value in paths:
            path = Path(value)
            identities[value] = path_identity(path, cache) if path.exists() or path.is_symlink() else None
    partial = {"launcher": launcher, "mounts": mounts, "loader_inputs": loader, "cpuinfo": cpu_profile()}
    if expected is not None and any(partial[key] != expected[key] for key in partial):
        raise IntegrityError("frozen host execution roots drifted")
    if expected is not None:
        for dependency in expected["launcher_dependencies"]:
            if path_identity(Path(dependency["path"]), cache) != dependency:
                raise IntegrityError("frozen launcher dynamic dependencies drifted")
    dependencies = [path_identity(path, cache) for path in launcher_dependencies(Path(launcher["resolved"]))]
    result = {"version": 1, **partial, "launcher_dependencies": dependencies}
    if expected is not None and result != expected:
        raise IntegrityError("frozen launcher dynamic dependencies drifted")
    return result
