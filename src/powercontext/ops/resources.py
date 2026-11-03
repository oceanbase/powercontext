# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Fixed producer resources: bounded acquisition, verification, and private staging.

The installer owns publication and recovery. This module never executes a
resource, resolves dependencies, changes an active tree, or writes a journal.
"""

# ruff: noqa: TRY301 - boundary normalization preserves stable resource error codes

from __future__ import annotations

import gzip
import hashlib
import io
import os
import platform
import re
import shutil
import stat
import sys
import tarfile
import time
import unicodedata
import zipfile
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO, Any, Literal, Self, cast
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import httpx
from packaging.version import Version
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator
from typing_extensions import override

from powercontext.ops.protocol import PROTOCOL_VERSION, OpsError, decode, digest, identifier
from powercontext.service.environment import protect_directory, read_protected_file

MAX_CATALOG_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_BYTES = 2 * 1024**3
MAX_UNPACKED_BYTES = 8 * 1024**3
MAX_FILES = 100_000
_CHUNK = 1024 * 1024
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_DEVICE = re.compile(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?\Z", re.IGNORECASE)


class ResourceError(OpsError):
    """Stable resource failure without URLs, credentials, or local paths."""


def _relative(name: str) -> str:
    parts = name.split("/")
    if (
        not name
        or len(name) > 1024
        or any(not part or part in {".", ".."} or part.endswith((".", " ")) for part in parts)
        or any(_DEVICE.fullmatch(part) for part in parts)
        or any(character in name for character in '\\:<>"|?*')
        or any(ord(character) < 32 for character in name)
        or unicodedata.normalize("NFC", name) != name
    ):
        raise ResourceError("resource_invalid")
    return name


def _origin(url: str) -> str:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise ResourceError("resource_source_invalid") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or "\\" in url
        or any(ord(character) < 33 for character in url)
    ):
        raise ResourceError("resource_source_invalid")
    return f"https://{parsed.hostname.lower()}:{port or 443}"


class ResourceFile(BaseModel):
    """One regular file, including the executable mode on POSIX."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    path: StrictStr
    size: StrictInt = Field(ge=0, le=MAX_UNPACKED_BYTES)
    sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    executable: StrictBool = False

    @model_validator(mode="after")
    def safe_path(self) -> Self:
        _relative(self.path)
        return self


class ResourceSpec(BaseModel):
    """Producer-attested complete Server bundle; references are not signature proof."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    resource_id: StrictStr
    server_version: StrictStr = Field(min_length=1, max_length=128)
    python_version: StrictStr = Field(min_length=1, max_length=128)
    platform: Literal["windows", "macos"]
    architecture: Literal["x86_64", "arm64"]
    archive_format: Literal["zip", "tar.gz"]
    url: StrictStr = Field(max_length=4096)
    allowed_origins: tuple[StrictStr, ...] = Field(min_length=1, max_length=16)
    archive_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    archive_size: StrictInt = Field(gt=0, le=MAX_ARCHIVE_BYTES)
    unpacked_size: StrictInt = Field(gt=0, le=MAX_UNPACKED_BYTES)
    python_relative: StrictStr
    ops_protocol_min: StrictInt = Field(ge=1)
    ops_protocol_max: StrictInt = Field(ge=1)
    source_refs: tuple[StrictStr, ...] = Field(min_length=1, max_length=32)
    license_refs: tuple[StrictStr, ...] = Field(min_length=1, max_length=32)
    qualification_ref: StrictStr = Field(min_length=1, max_length=1024)
    files: tuple[ResourceFile, ...] = Field(min_length=1, max_length=MAX_FILES)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        identifier(self.resource_id)
        Version(self.server_version)
        if not Version("3.11") <= Version(self.python_version) < Version("4"):
            raise ResourceError("resource_invalid")
        _relative(self.python_relative)
        origins = {_origin(url) for url in self.allowed_origins}
        if _origin(self.url) not in origins:
            raise ResourceError("resource_source_invalid")
        for url in self.allowed_origins:
            parsed = urlsplit(url)
            if parsed.path not in ("", "/") or parsed.query:
                raise ResourceError("resource_source_invalid")
        if self.ops_protocol_min > self.ops_protocol_max or sum(file.size for file in self.files) != self.unpacked_size:
            raise ResourceError("resource_invalid")
        if any(not reference.strip() or len(reference) > 4096 for reference in (*self.source_refs, *self.license_refs)):
            raise ResourceError("resource_invalid")
        _validate_file_paths(self.files)
        executable = next((file for file in self.files if file.path == self.python_relative), None)
        if executable is None or not executable.executable:
            raise ResourceError("resource_invalid")
        if self.platform == "windows":
            console = next((file for file in self.files if file.path == self.python_console_relative), None)
            if PurePosixPath(self.python_relative).name != "pythonw.exe" or console is None or not console.executable:
                raise ResourceError("resource_invalid")
        return self

    @property
    def python_console_relative(self) -> str:
        return (
            str(PurePosixPath(self.python_relative).with_name("python.exe"))
            if self.platform == "windows"
            else self.python_relative
        )

    @property
    def digest(self) -> str:
        return digest(self.model_dump(mode="json"))

    def as_dict(self) -> dict[str, object]:
        """Bounded plan summary; the full manifest never enters the Ops wire protocol."""
        return {
            "resource_id": self.resource_id,
            "resource_digest": self.digest,
            "server_version": self.server_version,
            "python_version": self.python_version,
            "platform": self.platform,
            "architecture": self.architecture,
            "archive_sha256": self.archive_sha256,
            "archive_size": self.archive_size,
            "unpacked_size": self.unpacked_size,
            "python_relative": self.python_relative,
            "qualification_ref": self.qualification_ref,
        }


def _validate_file_paths(manifest: tuple[ResourceFile, ...]) -> None:
    files = {file.path.casefold(): file for file in manifest}
    if len(files) != len(manifest):
        raise ResourceError("resource_invalid")
    directories: dict[str, str] = {}
    for file in manifest:
        for parent in PurePosixPath(file.path).parents:
            if parent == PurePosixPath("."):
                continue
            name = str(parent)
            key = name.casefold()
            if key in files or (key in directories and directories[key] != name):
                raise ResourceError("resource_invalid")
            directories[key] = name


@dataclass(frozen=True)
class ResourceCatalog:
    sha256: str
    resources: tuple[ResourceSpec, ...]

    @property
    def digest(self) -> str:
        return self.sha256

    @classmethod
    def read(cls, path: Path, expected_sha256: str) -> ResourceCatalog:
        """Read a producer-controlled catalog pinned by trusted distribution input."""
        if _HASH.fullmatch(expected_sha256) is None:
            raise ResourceError("resource_catalog_invalid")
        try:
            content = read_protected_file(path, max_bytes=MAX_CATALOG_BYTES).content
            if hashlib.sha256(content).hexdigest() != expected_sha256:
                raise ResourceError("resource_catalog_invalid")
            payload = decode(content)
            if not isinstance(payload, dict):
                raise ResourceError("resource_catalog_invalid")
            payload = cast(dict[str, Any], payload)
            if (
                set(payload) != {"format_version", "resources"}
                or type(payload["format_version"]) is not int
                or payload["format_version"] != 1
                or not isinstance(payload["resources"], list)
                or not 1 <= len(payload["resources"]) <= 32
            ):
                raise ResourceError("resource_catalog_invalid")
            resources = tuple(ResourceSpec.model_validate(item) for item in payload["resources"])
            if len({spec.resource_id for spec in resources}) != len(resources):
                raise ResourceError("resource_catalog_invalid")
            return cls(expected_sha256, resources)
        except (OSError, ValueError) as error:
            raise ResourceError("resource_catalog_invalid") from error

    def select(self, resource_id: str) -> ResourceSpec:
        matches = [resource for resource in self.resources if resource.resource_id == resource_id]
        if len(matches) != 1:
            raise ResourceError("resource_unavailable")
        _require_current(matches[0])
        return matches[0]

    def select_current(self) -> ResourceSpec:
        operating_system, architecture = _current_platform()
        matches = [
            resource
            for resource in self.resources
            if resource.platform == operating_system and resource.architecture == architecture
        ]
        if len(matches) != 1:
            raise ResourceError("resource_unavailable")
        _require_current(matches[0])
        return matches[0]


def _current_platform() -> tuple[str, str]:
    operating_system = {"win32": "windows", "darwin": "macos"}.get(sys.platform)
    architecture = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}.get(
        platform.machine().lower()
    )
    if operating_system is None or architecture is None:
        raise ResourceError("resource_platform_unsupported")
    return operating_system, architecture


def _require_current(spec: ResourceSpec) -> None:
    if (spec.platform, spec.architecture) != _current_platform():
        raise ResourceError("resource_platform_unsupported")
    if not spec.ops_protocol_min <= PROTOCOL_VERSION <= spec.ops_protocol_max:
        raise ResourceError("resource_protocol_unsupported")


def _cancel(cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise ResourceError("resource_cancelled")


def _regular(path: Path) -> os.stat_result:
    status = path.lstat()
    if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1 or getattr(status, "st_file_attributes", 0) & 0x400:
        raise ResourceError("resource_unsafe_path")
    return status


def _tree(path: Path) -> Iterator[tuple[str, Path]]:
    def failed(error: OSError) -> None:
        raise error

    for parent, directories, filenames in os.walk(path, followlinks=False, onerror=failed):
        for name in directories:
            directory = Path(parent) / name
            status = directory.lstat()
            if not stat.S_ISDIR(status.st_mode) or getattr(status, "st_file_attributes", 0) & 0x400:
                raise ResourceError("resource_unsafe_path")
        for name in filenames:
            file = Path(parent) / name
            _regular(file)
            yield file.relative_to(path).as_posix(), file


def validate_owned_tree(path: Path) -> dict[str, int]:
    """Validate a private root and link-free regular descendants; never create paths."""
    try:
        root = protect_directory(path)
        status = root.stat()
        for _name, _file in _tree(root):
            pass
        after = root.stat()
        if (status.st_dev, status.st_ino) != (after.st_dev, after.st_ino):
            raise ResourceError("resource_unsafe_path")
    except (OSError, ValueError) as error:
        raise ResourceError("resource_unsafe_path") from error
    return {"device": status.st_dev, "inode": status.st_ino}


def _file_hash(path: Path, cancelled: Callable[[], bool]) -> str:
    before = _regular(path)
    hashed = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK):
            _cancel(cancelled)
            hashed.update(chunk)
        opened = os.fstat(stream.fileno())
    after = _regular(path)
    identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
    if identity(before) != identity(opened) or identity(before) != identity(after):
        raise ResourceError("resource_corrupt")
    return hashed.hexdigest()


def verify(spec: ResourceSpec, directory: Path, *, cancelled: Callable[[], bool] = lambda: False) -> None:
    """Fully verify all files and reject extra content; no imports or writes."""
    try:
        protect_directory(directory)
        before = directory.stat()
        expected = {file.path: file for file in spec.files}
        allowed_directories = {str(parent) for file in spec.files for parent in PurePosixPath(file.path).parents}
        for parent, directories, _filenames in os.walk(directory, followlinks=False):
            for name in directories:
                if (Path(parent) / name).relative_to(directory).as_posix() not in allowed_directories:
                    raise ResourceError("resource_corrupt")
        found: set[str] = set()
        for name, path in _tree(directory):
            _cancel(cancelled)
            file = expected.get(name)
            if (
                file is None
                or path.stat().st_size != file.size
                or _file_hash(path, cancelled) != file.sha256
                or (os.name != "nt" and bool(path.stat().st_mode & stat.S_IXUSR) != file.executable)
            ):
                raise ResourceError("resource_corrupt")
            found.add(name)
        if found != set(expected):
            raise ResourceError("resource_corrupt")
        protect_directory(directory)
        after = directory.stat()
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise ResourceError("resource_unsafe_path")
    except ResourceError:
        raise
    except (OSError, ValueError) as error:
        raise ResourceError("resource_corrupt") from error


@dataclass(frozen=True)
class PreparedResource:
    directory: Path
    spec: ResourceSpec
    identity: dict[str, int]


def validate_destination(spec: ResourceSpec, root: Path) -> None:
    """Reject unsupported ordinary Windows paths before acquisition or writes.

    Native service launch does not promise extended-path support. The producer
    must qualify a layout that fits both staging and the final installation.
    """
    if sys.platform != "win32":
        return
    root = Path(os.path.abspath(root))
    if str(root).startswith("\\\\?\\"):
        raise ResourceError("resource_path_unsupported")

    def check(path: Path, limit: int) -> None:
        if len(str(path).encode("utf-16-le")) // 2 > limit or any(
            len(part.encode("utf-16-le")) // 2 > 255 for part in path.parts
        ):
            raise ResourceError("resource_path_unsupported")

    check(root, 247)  # Ordinary CreateDirectory reserves room for an 8.3 name.
    directories = {root}
    for file in spec.files:
        path = root / file.path
        check(path, 259)
        parent = path.parent
        while parent not in directories:
            check(parent, 247)
            directories.add(parent)
            parent = parent.parent


class ResourceStore:
    """Disposable hash-addressed cache; interrupted partial downloads are never reused."""

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = Path(os.path.abspath(cache_dir))

    def prepare(
        self, spec: ResourceSpec, staging_parent: Path, *, cancelled: Callable[[], bool] = lambda: False
    ) -> PreparedResource:
        _require_current(spec)
        _cancel(cancelled)
        stage = Path(os.path.abspath(staging_parent)) / ("resource-" + uuid4().hex)
        validate_destination(spec, stage)
        stage_identity: dict[str, int] | None = None
        parent_identity: os.stat_result | None = None
        transferred = False
        try:
            parent = protect_directory(staging_parent, create=True)
            parent_identity = parent.stat()
            archive = self._archive(spec, cancelled)
            protect_directory(parent)
            current_parent = parent.stat()
            if (parent_identity.st_dev, parent_identity.st_ino) != (current_parent.st_dev, current_parent.st_ino):
                raise ResourceError("resource_unsafe_path")
            # An unrecorded stage is never implicitly adopted. The caller can
            # retain a returned identity in its existing operation checkpoint.
            protect_directory(stage, create=True)
            stage_identity = _identity(stage.stat())
            self._extract(spec, archive, stage, cancelled)
            verify(spec, stage, cancelled=cancelled)
            protect_directory(parent)
            after = parent.stat()
            if (parent_identity.st_dev, parent_identity.st_ino) != (after.st_dev, after.st_ino):
                raise ResourceError("resource_unsafe_path")
            prepared = PreparedResource(stage, spec, validate_owned_tree(stage))
        except ResourceError:
            raise
        except (
            OSError,
            ValueError,
            EOFError,
            NotImplementedError,
            tarfile.TarError,
            zipfile.BadZipFile,
            zlib.error,
        ) as error:
            raise ResourceError("resource_prepare_failed") from error
        else:
            transferred = True
            return prepared
        finally:
            if not transferred and stage_identity is not None and parent_identity is not None:
                _discard_created(stage, _identity(parent_identity), stage_identity, directory=True)

    def _archive(self, spec: ResourceSpec, cancelled: Callable[[], bool]) -> Path:
        protect_directory(self.cache_dir, create=True)
        cached = self.cache_dir / (spec.archive_sha256 + ".archive")
        if cached.exists() or cached.is_symlink():
            if _regular(cached).st_size == spec.archive_size and _file_hash(cached, cancelled) == spec.archive_sha256:
                return cached
            # Only this exact regular cache object is disposable, not an
            # active environment or an arbitrary caller-supplied path.
            cached.unlink()
        partial = self.cache_dir / (spec.archive_sha256 + "." + uuid4().hex + ".partial")
        parent_identity = _identity(self.cache_dir.stat())
        partial_identity = self._download(spec, partial, cancelled)
        try:
            if _regular(partial).st_size != spec.archive_size or _file_hash(partial, cancelled) != spec.archive_sha256:
                raise ResourceError("resource_corrupt")
            protect_directory(self.cache_dir)
            os.replace(partial, cached)
        finally:
            _discard_created(partial, parent_identity, partial_identity, directory=False)
        return cached

    @staticmethod
    def _download(spec: ResourceSpec, destination: Path, cancelled: Callable[[], bool]) -> dict[str, int]:
        allowed = {_origin(url) for url in spec.allowed_origins}
        url = spec.url
        deadline = time.monotonic() + 1800
        try:
            with httpx.Client(trust_env=False, follow_redirects=False, timeout=30) as client:
                for _redirect in range(6):
                    _cancel(cancelled)
                    if _origin(url) not in allowed:
                        raise ResourceError("resource_source_invalid")
                    with client.stream("GET", url, headers={"Accept-Encoding": "identity"}) as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            location = response.headers.get("location")
                            if not location:
                                raise ResourceError("resource_unavailable")
                            url = urljoin(url, location)
                            continue
                        return _receive(response, destination, spec.archive_size, deadline, cancelled)
                raise ResourceError("resource_source_invalid")
        except httpx.HTTPError as error:
            raise ResourceError("resource_unavailable") from error

    @staticmethod
    def _extract(spec: ResourceSpec, archive: Path, stage: Path, cancelled: Callable[[], bool]) -> None:
        extraction = _Extraction(spec, stage, cancelled)
        if spec.archive_format == "zip":
            extraction.zip(archive)
        else:
            extraction.tar(archive)


def _receive(
    response: httpx.Response, destination: Path, expected: int, deadline: float, cancelled: Callable[[], bool]
) -> dict[str, int]:
    if response.status_code != 200 or response.headers.get("content-encoding", "identity") != "identity":
        raise ResourceError("resource_unavailable")
    length = response.headers.get("content-length")
    if length is not None and length != str(expected):
        raise ResourceError("resource_corrupt")
    size = 0
    identity: dict[str, int] | None = None
    parent_identity = _identity(destination.parent.stat())
    try:
        with destination.open("xb") as output:
            identity = _identity(os.fstat(output.fileno()))
            for chunk in response.iter_raw():
                _cancel(cancelled)
                if time.monotonic() > deadline:
                    raise ResourceError("resource_timeout")
                size += len(chunk)
                if size > expected:
                    raise ResourceError("resource_corrupt")
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if size != expected:
            raise ResourceError("resource_corrupt")
    except BaseException:
        if identity is not None:
            _discard_created(destination, parent_identity, identity, directory=False)
        raise
    return identity


def _identity(status: os.stat_result) -> dict[str, int]:
    return {"device": status.st_dev, "inode": status.st_ino}


def _discard_created(path: Path, parent_identity: dict[str, int], identity: dict[str, int], *, directory: bool) -> None:
    """Best-effort removal only while this attempt still owns the exact object."""
    try:
        parent = protect_directory(path.parent)
        if _identity(parent.stat()) != parent_identity or _identity(path.lstat()) != identity:
            return
        # Check the absolute resolved target before any recursive removal.
        resolved = path.resolve()
        if not path.is_absolute() or resolved.parent != parent.resolve() or resolved == parent.resolve():
            return
        if directory:
            if validate_owned_tree(path) != identity:
                return
            shutil.rmtree(path)
        else:
            _regular(path)
            path.unlink()
    except (OSError, ValueError):
        # Replacement, unsafe ownership and permission failures do not
        # authorize broader cleanup or hide the original preparation error.
        return


class _Extraction:
    """Enforce the same manifest and byte bounds for both archive formats."""

    def __init__(self, spec: ResourceSpec, stage: Path, cancelled: Callable[[], bool]) -> None:
        self.expected = {file.path: file for file in spec.files}
        self.directories = {str(parent) for file in spec.files for parent in PurePosixPath(file.path).parents}
        self.seen: set[str] = set()
        self.stage = stage
        self.cancelled = cancelled
        # Include tar headers, padding and bounded producer metadata, without
        # permitting an arbitrarily expanding PAX header before a file entry.
        self.tar_limit = spec.unpacked_size + len(spec.files) * 4096 + 1024 * 1024

    def entry(self, name: str, size: int, directory: bool) -> ResourceFile | None:
        _cancel(self.cancelled)
        name = _relative(name.removesuffix("/") if directory else name)
        if len(self.seen) >= MAX_FILES * 2 or name.casefold() in self.seen:
            raise ResourceError("resource_archive_invalid")
        self.seen.add(name.casefold())
        if directory:
            if name not in self.directories or size != 0:
                raise ResourceError("resource_archive_invalid")
            return None
        file = self.expected.get(name)
        if file is None or size != file.size:
            raise ResourceError("resource_archive_invalid")
        return file

    def copy(self, file: ResourceFile, source: IO[bytes]) -> None:
        destination = self.stage / file.path
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        total = 0
        hashed = hashlib.sha256()
        with destination.open("xb") as output:
            while chunk := source.read(min(_CHUNK, file.size - total + 1)):
                _cancel(self.cancelled)
                total += len(chunk)
                if total > file.size:
                    raise ResourceError("resource_archive_invalid")
                hashed.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if total != file.size or hashed.hexdigest() != file.sha256:
            raise ResourceError("resource_corrupt")
        destination.chmod(0o700 if file.executable else 0o600)

    def zip(self, path: Path) -> None:
        with zipfile.ZipFile(path) as archive:
            if len(archive.infolist()) > MAX_FILES * 2:
                raise ResourceError("resource_archive_invalid")
            for member in archive.infolist():
                kind = stat.S_IFMT(member.external_attr >> 16)
                if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or member.flag_bits & 1 or member.external_attr & 0x400:
                    raise ResourceError("resource_archive_invalid")
                file = self.entry(member.filename, member.file_size, member.is_dir())
                if file is not None:
                    with archive.open(member) as stream:
                        self.copy(file, stream)

    def tar(self, path: Path) -> None:
        with (
            gzip.open(path, "rb") as compressed,
            tarfile.open(fileobj=_BoundedTar(compressed, self.tar_limit, self.cancelled), mode="r|") as archive,
        ):
            for member in archive:
                if not (member.isfile() or member.isdir()) or member.issparse():
                    raise ResourceError("resource_archive_invalid")
                file = self.entry(member.name, member.size, member.isdir())
                if file is not None:
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise ResourceError("resource_archive_invalid")
                    with stream:
                        self.copy(file, stream)


class _BoundedTar(io.RawIOBase):
    def __init__(self, stream: gzip.GzipFile, limit: int, cancelled: Callable[[], bool]) -> None:
        self.stream = stream
        self.remaining = limit
        self.cancelled = cancelled

    @override
    def read(self, size: int = -1) -> bytes:
        _cancel(self.cancelled)
        requested = self.remaining + 1 if size < 0 else min(size, self.remaining + 1)
        content = self.stream.read(requested)
        self.remaining -= len(content)
        if self.remaining < 0:
            raise ResourceError("resource_archive_invalid")
        return content
