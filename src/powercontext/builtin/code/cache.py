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

"""Private, rebuildable local caches with process locks and atomic publication."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import stat
import uuid
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from pathlib import Path
from time import monotonic
from typing import TypeVar

from powercontext.builtin.code.errors import CodeUnavailableError, UnsupportedCodeCapabilityError
from powercontext.builtin.code.models import CodeValue, Digest
from powercontext.builtin.code.repository import RepositorySnapshot

_T = TypeVar("_T")


class IndexManifest(CodeValue):
    format: int = 1
    scope_id: str
    configuration_digest: Digest
    engine_digest: Digest
    index_digest: Digest
    snapshot: RepositorySnapshot
    indexed_files: int
    parse_failures: int
    unresolved_references: int

    def fingerprint(self) -> str:
        return digest_json(self.model_dump(mode="json"))


def digest_json(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


@asynccontextmanager
async def file_lock(path: Path, *, deadline: float) -> AsyncIterator[None]:
    """Never leave an executor thread waiting on a lock after request timeout."""

    if os.name != "posix":
        raise UnsupportedCodeCapabilityError("unsupported_capability")
    import fcntl

    descriptor = await asyncio.to_thread(_open_lock, path)
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if monotonic() >= deadline:
                    raise CodeUnavailableError("code_timeout") from None
                await asyncio.sleep(min(0.05, max(0, deadline - monotonic())))
        if monotonic() >= deadline:
            raise CodeUnavailableError("code_timeout")
        yield
    finally:
        os.close(descriptor)


def _open_lock(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)


def directory_size(root: Path, *, deadline: float) -> int:
    """Measure all retained and temporary files, rejecting cache symlinks."""

    total = 0
    for directory, directories, files in os.walk(root, followlinks=False):
        if monotonic() >= deadline:
            raise CodeUnavailableError("code_timeout")
        for name in (*directories, *files):
            metadata = (Path(directory) / name).lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise CodeUnavailableError("code_cache_invalid")
            if stat.S_ISREG(metadata.st_mode):
                total += metadata.st_size
    return total


def index_digest(root: Path, *, deadline: float) -> str:
    digest = hashlib.sha256()
    index = root / ".codegraph"
    if index.is_symlink() or not index.is_dir():
        raise CodeUnavailableError("code_cache_invalid")
    for path in sorted(index.rglob("*")):
        if path.is_symlink():
            raise CodeUnavailableError("code_cache_invalid")
        if not path.is_file():
            continue
        digest.update(path.relative_to(index).as_posix().encode() + b"\0")
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                if monotonic() >= deadline:
                    raise CodeUnavailableError("code_timeout")
                digest.update(chunk)
    return digest.hexdigest()


class CodeCache:
    """Serialize allocation and pin complete generations until a request ends.

    Allocation is conservative: builds and engine queries share one cache lock.
    This bounds retained caches plus temporary engine copies across Scopes. It
    also lets reclamation safely remove old generations without lease records.
    """

    def __init__(self, root: Path, scope_id: str, *, max_bytes: int) -> None:
        self.root = root
        self.scope = root / hashlib.sha256(scope_id.encode()).hexdigest()
        self.max_bytes = max_bytes

    @asynccontextmanager
    async def locked(self, *, deadline: float) -> AsyncIterator[None]:
        try:
            async with (
                file_lock(self.scope / "scope.lock", deadline=deadline),
                file_lock(self.root / "allocation.lock", deadline=deadline),
            ):
                await asyncio.to_thread(self._cleanup, deadline)
                yield
        except OSError:
            raise CodeUnavailableError("code_cache_unavailable") from None

    @asynccontextmanager
    async def building(self) -> AsyncIterator[None]:
        marker = self.scope / "building"
        await asyncio.to_thread(marker.write_text, "building")
        try:
            yield
        finally:
            await asyncio.to_thread(marker.unlink, missing_ok=True)

    def is_building(self) -> bool:
        if os.name != "posix" or not (self.scope / "building").is_file():
            return False
        import fcntl

        descriptor = _open_lock(self.scope / "scope.lock")
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            return False
        finally:
            os.close(descriptor)

    def _cleanup(self, deadline: float) -> None:
        current = self._current_name()
        for path in self.scope.iterdir():
            if monotonic() >= deadline:
                raise CodeUnavailableError("code_timeout")
            if path.name.startswith(("stage-", "query-", "generation-")) and path.name != current:
                if path.is_symlink():
                    raise CodeUnavailableError("code_cache_invalid")
                shutil.rmtree(path)

    def _current_name(self) -> str | None:
        try:
            value = (self.scope / "current").read_text()
        except FileNotFoundError:
            return None
        prefix, _, suffix = value.partition("-")
        if prefix != "generation" or len(suffix) != 32 or any(char not in "0123456789abcdef" for char in suffix):
            raise CodeUnavailableError("code_cache_invalid")
        return value

    def current(self) -> tuple[Path, IndexManifest]:
        name = self._current_name()
        if name is None:
            raise CodeUnavailableError("code_index_missing")
        directory = self.scope / name
        try:
            manifest = IndexManifest.model_validate_json((directory / "manifest.json").read_bytes())
        except (OSError, ValueError):
            raise CodeUnavailableError("code_cache_invalid") from None
        return directory, manifest

    def stage(self) -> Path:
        directory = self.scope / ("stage-" + uuid.uuid4().hex)
        directory.mkdir(mode=0o700)
        return directory

    def publish(self, directory: Path, manifest: IndexManifest) -> Path:
        manifest_path = directory / "manifest.json"
        manifest_path.write_text(manifest.model_dump_json())
        manifest_path.chmod(0o600)
        # Atomic rename exposes only completed capture + index + manifest. An
        # interrupted pointer update leaves the previous complete generation.
        published = self.scope / ("generation-" + uuid.uuid4().hex)
        directory.rename(published)
        pointer = self.scope / "current.tmp"
        pointer.write_text(published.name)
        pointer.chmod(0o600)
        pointer.replace(self.scope / "current")
        return published

    async def check_capacity(self, *, deadline: float, reserve_bytes: int = 0) -> None:
        size = await asyncio.to_thread(directory_size, self.root, deadline=deadline)
        if size + reserve_bytes > self.max_bytes:
            raise CodeUnavailableError("code_cache_limit")

    async def bounded(self, operation: Awaitable[_T], *, deadline: float) -> _T:
        """Terminate a growing engine build before publishing an oversized cache."""

        running = asyncio.ensure_future(operation)
        monitor = asyncio.create_task(self._monitor(deadline))
        try:
            done, _ = await asyncio.wait((running, monitor), return_when=asyncio.FIRST_COMPLETED)
            if monitor in done:
                await monitor
            result = await running
            await self.check_capacity(deadline=deadline)
            return result
        finally:
            for task in (running, monitor):
                task.cancel()
            await asyncio.gather(running, monitor, return_exceptions=True)

    async def _monitor(self, deadline: float) -> None:
        while True:
            await asyncio.sleep(0.1)
            await self.check_capacity(deadline=deadline)
