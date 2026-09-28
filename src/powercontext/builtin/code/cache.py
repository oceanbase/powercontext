# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Immutable generations, cross-process reader leases, and atomic publication."""

from __future__ import annotations

import json
import os
import shutil
import stat
import time
import uuid
from collections.abc import Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from powercontext.builtin.code.capture import check_deadline, digest_bytes, json_bytes, write_private
from powercontext.builtin.code.errors import CodeError
from powercontext.builtin.code.graph import GraphReader, GraphStore
from powercontext.builtin.code.store import SQLiteGraphStore
from powercontext.builtin.code.telemetry import stage


@contextmanager
def file_lock(path: Path, deadline: float, *, shared: bool = False, wait: bool = True) -> Iterator[None]:
    try:
        import fcntl
    except ImportError as error:
        raise CodeError("unsupported_code_platform") from error
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        while True:
            try:
                fcntl.flock(descriptor, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
                break
            except BlockingIOError as error:
                if not wait:
                    raise CodeError("code_cache_busy") from error
                check_deadline(deadline)
                time.sleep(min(0.02, max(0, deadline - time.monotonic())))
        yield
    finally:
        os.close(descriptor)


def hash_file(path: Path, deadline: float) -> str:
    import hashlib

    digest = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise CodeError("index_integrity_failed")
        while chunk := source.read(128 * 1024):
            check_deadline(deadline)
            digest.update(chunk)
    return digest.hexdigest()


def read_private(path: Path, *, maximum: int = 64 * 1024 * 1024) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise CodeError("index_integrity_failed")
            content = source.read(maximum + 1)
        if len(content) > maximum:
            raise CodeError("index_integrity_failed")
    except OSError as error:
        raise CodeError("index_integrity_failed") from error
    return content


def read_json(path: Path, *, maximum: int = 64 * 1024 * 1024) -> dict[str, Any]:
    try:
        value = json.loads(read_private(path, maximum=maximum))
    except (UnicodeError, ValueError) as error:
        raise CodeError("index_integrity_failed") from error
    if not isinstance(value, dict):
        raise CodeError("index_integrity_failed")
    return value


def generation_name(reference: dict[str, Any]) -> str:
    name = reference.get("directory", "")
    if not isinstance(name, str) or len(name) != 43 or not name.startswith("generation-"):
        raise CodeError("index_integrity_failed")
    if any(character not in "0123456789abcdef" for character in name[11:]):
        raise CodeError("index_integrity_failed")
    return name


@dataclass(frozen=True)
class Generation:
    directory: Path
    manifest: dict[str, Any]
    store: GraphStore = field(default_factory=SQLiteGraphStore)

    @property
    def fingerprint(self) -> str:
        return self.manifest["fingerprint"]

    def graph(self, deadline: float) -> AbstractContextManager[GraphReader]:
        return self.store.open(self.directory, self.manifest, deadline)

    def source(self, path: str, deadline: float) -> bytes:
        record = self.manifest["included_files"].get(path)
        if record is None:
            raise CodeError("code_target_missing", status=422)
        source = self.directory / "source" / record["sha256"]
        content = read_private(source, maximum=record["size"])
        check_deadline(deadline)
        if digest_bytes(content) != record["sha256"]:
            raise CodeError("index_integrity_failed")
        return content


class GenerationCache:
    def __init__(self, directory: Path, store: GraphStore | None = None) -> None:
        self.directory = directory
        self.store = store if store is not None else SQLiteGraphStore()

    def initialize(self) -> None:
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        if self.directory.is_symlink():
            raise CodeError("invalid_code_cache")
        self.directory.chmod(0o700)

    def pointer(self) -> dict[str, Any] | None:
        if not (self.directory / "current.json").exists():
            return None
        pointer = read_json(self.directory / "current.json", maximum=4096)
        for key in ("current", "previous"):
            reference = pointer.get(key)
            if key == "previous" and reference is None:
                continue
            if not isinstance(reference, dict):
                raise CodeError("index_integrity_failed")
            generation_name(reference)
            checksum = reference.get("manifest_sha256")
            if not isinstance(checksum, str) or len(checksum) != 64:
                raise CodeError("index_integrity_failed")
        return pointer

    def load(self, reference: dict[str, Any], deadline: float) -> Generation:
        directory = self.directory / generation_name(reference)
        if directory.is_symlink():
            raise CodeError("index_integrity_failed")
        manifest_file = directory / "manifest.json"
        content = read_private(manifest_file)
        if digest_bytes(content) != reference.get("manifest_sha256"):
            raise CodeError("index_integrity_failed")
        try:
            manifest = json.loads(content)
            if not isinstance(manifest, dict):
                raise CodeError("index_integrity_failed")
            self.store.verify(directory, manifest, deadline)
        except (OSError, ValueError) as error:
            raise CodeError("index_integrity_failed") from error
        return Generation(directory, manifest, self.store)

    @contextmanager
    def pin(self, deadline: float, *, previous: bool = False) -> Iterator[tuple[Generation, Generation | None]]:
        self.initialize()
        with ExitStack() as readers:
            with file_lock(self.directory / "publish.lock", deadline, shared=True):
                pointer = self.pointer()
                if pointer is None:
                    raise CodeError("code_index_missing")
                current = self._lease(pointer["current"], readers, deadline)
                before = self._previous(pointer, readers, deadline) if previous else None
            yield current, before

    def _lease(self, reference: dict[str, Any], readers: ExitStack, deadline: float) -> Generation:
        generation = self.load(reference, deadline)
        readers.enter_context(file_lock(generation.directory / "reader.lock", deadline, shared=True))
        return generation

    def _previous(self, pointer: dict[str, Any], readers: ExitStack, deadline: float) -> Generation:
        reference = pointer.get("previous")
        if reference is None:
            raise CodeError("baseline_unavailable", status=409)
        return self._lease(reference, readers, deadline)

    def staging(self) -> Path:
        path = self.directory / ("staging-" + uuid.uuid4().hex)
        path.mkdir(mode=0o700)
        for child in ("source", "facts", "diagnostics"):
            (path / child).mkdir(mode=0o700)
        return path

    def publish(self, staging: Path, manifest: dict[str, Any], deadline: float, max_bytes: int) -> Generation:
        manifest_content = json_bytes(manifest)
        write_private(staging / "manifest.json", manifest_content)
        write_private(staging / "reader.lock", b"")
        # Persist directory entries before making a generation reachable from current.json.
        for path in (staging / "source", staging / "facts", staging / "diagnostics", staging):
            descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        with file_lock(self.directory / "publish.lock", deadline):
            try:
                pointer = self.pointer()
            except CodeError:
                pointer = None
            self.collect(pointer, deadline)
            with stage("cache_size") as attributes:
                size = self.size(deadline)
                attributes["cache_bytes"] = size
                if size > max_bytes:
                    raise CodeError("code_cache_limit_exceeded")
            directory = self.directory / ("generation-" + uuid.uuid4().hex)
            os.replace(staging, directory)
            reference = {"directory": directory.name, "manifest_sha256": digest_bytes(manifest_content)}
            self.replace_json(
                "current.json", {"current": reference, "previous": pointer["current"] if pointer else None}
            )
            self.collect(self.pointer(), deadline)
        return Generation(directory, manifest, self.store)

    def replace_json(self, name: str, value: dict[str, Any]) -> None:
        temporary = self.directory / ("pending-" + uuid.uuid4().hex)
        try:
            write_private(temporary, json_bytes(value))
            os.replace(temporary, self.directory / name)
            descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            temporary.unlink(missing_ok=True)

    def size(self, deadline: float) -> int:
        size = 0
        for path in self.directory.rglob("*"):
            check_deadline(deadline)
            if path.is_file() and not path.is_symlink():
                size += path.stat().st_size
        size += self.store.size(self.directory, deadline)
        return size

    def clear(self, deadline: float) -> dict[str, Any]:
        self.initialize()
        with file_lock(self.directory / "build.lock", deadline), file_lock(self.directory / "publish.lock", deadline):
            # Failed abandoned-build cleanup must not unpublish a usable generation.
            for path in self.directory.glob("staging-*"):
                if path.is_dir() and not path.is_symlink():
                    self.store.remove(path, deadline)
                    shutil.rmtree(path)
            # Keep lock inodes stable: removing the binding directory would let a
            # concurrent process acquire a different lock for this same binding.
            for name in ("current.json", "last-build.json"):
                (self.directory / name).unlink(missing_ok=True)
            descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self.collect(None, deadline)
            retained = sum(path.is_dir() and not path.is_symlink() for path in self.directory.glob("generation-*"))
            return {"status": "cleared", "retained_reader_generations": retained}

    def collect(self, pointer: dict[str, Any] | None, deadline: float) -> None:
        retained = {reference["directory"] for reference in (pointer or {}).values() if isinstance(reference, dict)}
        for path in self.directory.glob("generation-*"):
            if path.name in retained or path.is_symlink():
                continue
            try:
                with file_lock(path / "reader.lock", deadline, wait=False):
                    self.store.remove(path, deadline)
                    shutil.rmtree(path)
            except CodeError as error:
                if error.code != "code_cache_busy":
                    raise
