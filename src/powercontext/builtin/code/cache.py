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
    #: How many publication records a binding normally carries. The bound is
    #: enforced by recalling the commits it retires, never by forgetting a handle,
    #: so a binding that keeps many generations pinned may stand above it.
    PUBLICATION_WINDOW = 64

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

    def publications(self) -> dict[str, dict[str, Any]]:
        path = self.directory / "publication.json"
        if not path.exists():
            return {}
        records = read_json(path, maximum=64 * 1024).get("attempts")
        if not isinstance(records, dict):
            raise CodeError("index_integrity_failed")
        return {str(attempt): record for attempt, record in records.items() if isinstance(record, dict)}

    def begin_publication(self, staging: Path, deadline: float) -> str:
        """Name the attempt before the store writes any row under it.

        The identifier has to come from here rather than the store: a commit that
        has to be recalled later must be nameable even when the directory that
        carried its name is gone.
        """
        attempt = uuid.uuid4().hex
        records = self.publications()
        records[attempt] = {"directory": staging.name, "state": "pending", "started_at": time.time()}
        self._write_publications(records, deadline)
        return attempt

    def recover(self, deadline: float) -> int:
        """Reclaim commits whose generation directory no longer exists.

        ``collect()`` starts from directory names, so it cannot see a commit whose
        directory was removed out of band — a wiped cache, a pruned staging area.
        The records are the only place that still knows the identifier.

        A missing directory is not proof that the commit is unwanted. The pointer
        can still reach a generation whose manifest names the identifier these rows
        were committed under, and a reader may be holding it. Reclaiming on
        directory absence alone deletes live data, so that name is checked first.
        """
        records = self.publications()
        retained = self._retained_identifiers()
        abandoned: list[str] = []
        for attempt, record in records.items():
            if attempt in retained:
                continue
            recorded = self._recorded_directory(record)
            if recorded is None or not (self.directory / recorded).exists():
                abandoned.append(attempt)
        for attempt in abandoned:
            self.store.remove_generation(attempt, self.directory.name, deadline)
            records.pop(attempt)
        if abandoned or len(records) > self.PUBLICATION_WINDOW:
            self._write_publications(records, deadline)
        return len(abandoned)

    def _retained_identifiers(self) -> set[str]:
        """Identifiers whose rows a live generation still owns, read from the generations.

        A row is addressed by its identifier, not by the directory that carries it,
        so the manifest inside each generation directory is the only place that can
        say which rows a live generation owns. Two sources qualify:

        - the generations the pointer still reaches — that is the crash a record
          whose directory has gone can still point at;
        - every generation directory that still exists, whether or not the pointer
          names it any more. A reader lease can keep an older generation alive
          long after it left current/previous, and deleting rows by identifier
          while that directory exists turns the reader's successful query into an
          empty one. Only :meth:`collect` may remove a directory, and it does so
          under the reader lock, so a directory here is proof the rows are live.
        """

        try:
            pointer = self.pointer()
        except CodeError:
            pointer = None
        retained: set[str] = set()
        references = [reference for reference in (pointer or {}).values() if isinstance(reference, dict)]
        references.extend({"directory": path.name} for path in self.directory.glob("generation-*") if path.is_dir())
        for reference in references:
            directory = reference.get("directory")
            if not isinstance(directory, str):
                continue
            try:
                manifest = read_json(self.directory / directory / "manifest.json")
            except (CodeError, OSError):
                continue
            graph = manifest.get("graph")
            identifier = graph.get("id") if isinstance(graph, dict) else None
            if isinstance(identifier, str):
                retained.add(identifier)
        return retained

    @staticmethod
    def _recorded_directory(record: dict[str, Any]) -> str | None:
        """The cache-relative name a record gives its directory, if it names one.

        A record is written by this class, so a name that is not one of its two
        shapes is corruption, not an older format. Returning ``None`` instead of
        coercing to ``""`` matters: the cache root exists, so an empty name made
        every existence check succeed and the attempt unreclaimable. An unnamed
        record is treated exactly like a vanished directory — reclaimable, with
        the identifier as the only handle left.
        """

        name = record.get("directory")
        if not isinstance(name, str):
            return None
        for prefix, width in (("staging-", 40), ("generation-", 43)):
            if name.startswith(prefix) and len(name) == width:
                break
        else:
            return None
        if any(character not in "0123456789abcdef" for character in name[len(prefix) :]):
            return None
        return name

    def _write_publications(
        self, records: dict[str, dict[str, Any]], deadline: float, *, protected: frozenset[str] = frozenset()
    ) -> None:
        excess = len(records) - self.PUBLICATION_WINDOW
        if excess > 0:
            # A record is the only handle to rows that outlive their directory, so the
            # window recalls those commits instead of forgetting them. Three records are
            # never dropped: one whose directory still exists (a generation a reader may
            # hold), one whose identifier a live generation still owns, and one this
            # publisher has just named but not renamed into place yet — its directory is
            # missing by design between the record write and the rename, and reclaiming
            # it deletes the rows this very publication committed. The excess is allowed
            # to stand.
            live = self._retained_identifiers()

            def reclaimable(attempt: str, record: dict[str, Any]) -> bool:
                if attempt in protected or attempt in live:
                    return False
                recorded = self._recorded_directory(record)
                return recorded is None or not (self.directory / recorded).exists()

            stale = sorted(
                ((attempt, record) for attempt, record in records.items() if reclaimable(attempt, record)),
                key=lambda item: self._started_at(item[1]),
            )
            for attempt, _ in stale[:excess]:
                self.store.remove_generation(attempt, self.directory.name, deadline)
                records.pop(attempt)
        self.replace_json("publication.json", {"attempts": records})

    @staticmethod
    def _started_at(record: dict[str, Any]) -> float:
        # The record is read back from JSON, so the timestamp can be anything; sorting
        # must not raise on it. Unreadable stamps sort first — the oldest possible.
        value = record.get("started_at")
        return float(value) if isinstance(value, (int, float)) else 0.0

    def publish(
        self,
        staging: Path,
        manifest: dict[str, Any],
        deadline: float,
        max_bytes: int,
        *,
        attempt: str | None = None,
    ) -> Generation:
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
            if attempt is not None:
                # Name the generation before it exists. Recording the final name only
                # after the rename leaves a window where the record still points at a
                # staging directory that has already gone: recovery would then read
                # "directory missing" and delete the rows of a live generation.
                # Written first, the record can only be wrong in the harmless
                # direction — it names a directory that was never created, and the
                # rows it recalls are the ones this attempt committed.
                records = self.publications()
                records[attempt] = {
                    "directory": directory.name,
                    "state": "published",
                    "started_at": records.get(attempt, {}).get("started_at", time.time()),
                }
                # Protected: until os.replace below lands, the record's directory is
                # missing by design, and the window must not read that as stale.
                self._write_publications(records, deadline, protected=frozenset({attempt}))
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
            # A directory that was removed out of band leaves nothing for collect() to
            # start from; clearing is the last moment the binding is still named, so
            # the records have to be settled here or their rows are charged forever.
            self.recover(deadline)
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
