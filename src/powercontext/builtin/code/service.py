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

"""Scope-bound code lookup with content verification and no business writes."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from powercontext.builtin.code.adapter import CodeGraphAdapter, EngineIdentity
from powercontext.builtin.code.cache import CodeCache, IndexManifest, digest_json, directory_size, index_digest
from powercontext.builtin.code.config import CodeConfig
from powercontext.builtin.code.content import clip_item, fit_response, source_lines
from powercontext.builtin.code.errors import (
    CodeChangedError,
    CodeUnavailableError,
    InvalidCodeRequestError,
    UnsupportedCodeCapabilityError,
)
from powercontext.builtin.code.models import (
    CodeCoverage,
    CodeItem,
    CodeLocation,
    CodeQueryRequest,
    CodeQueryResponse,
    CodeReadOperation,
    CodeStatusResponse,
    CodeSymbolsOperation,
    CodeTreeOperation,
)
from powercontext.builtin.code.repository import RepositorySnapshot, capture_repository, read_file_bytes

_CAPABILITIES = ("tree", "symbols", "callers", "callees", "impact", "affected_tests", "read", "prepare")
_LIMITATIONS = (
    "Static Python analysis can miss dynamic calls and import aliases.",
    "Relationships involving same-name definitions are unavailable in this engine build.",
    "Only included UTF-8 Python files are analyzed; results describe the checked working tree, not later edits.",
)


class CodeService:
    """Require callers to authorize the current Scope before invoking this service."""

    def __init__(self, config: CodeConfig) -> None:
        self.config = config.model_copy(deep=True)
        self.adapter = CodeGraphAdapter(config.provider)

    def _repository(self, scope_id: str) -> Path:
        root = self.config.repositories.get(scope_id)
        if not self.config.enabled or root is None:
            raise CodeUnavailableError("code_not_configured")
        if not root.is_absolute():
            raise InvalidCodeRequestError("code_root_must_be_absolute")
        return root

    def _cache(self, scope_id: str) -> CodeCache:
        return CodeCache(self.config.cache_dir, scope_id, max_bytes=self.config.limits.max_cache_bytes)

    def _configuration_digest(self, scope_id: str) -> str:
        return digest_json({
            "scope_id": scope_id,
            "repository": str(self._repository(scope_id)),
            "include_untracked": self.config.include_untracked,
            "exclude": self.config.exclude,
            "max_file_bytes": self.config.limits.max_file_bytes,
            "max_files": self.config.limits.max_files,
            "max_content_bytes": self.config.limits.max_content_bytes,
            "policy": "python-utf8-no-links-raw-git-v2",
        })

    async def index(self, scope_id: str) -> CodeStatusResponse:
        """Explicit local refresh; HTTP prepare and query never build an index."""

        root = self._repository(scope_id)
        deadline = monotonic() + self.config.limits.build_timeout_seconds
        cache = self._cache(scope_id)
        async with cache.locked(deadline=deadline), cache.building():
            identity = await self.adapter.identity(deadline=deadline)
            snapshot = await capture_repository(root, self.config, deadline=deadline)
            if cached := await self._reusable(cache, scope_id, identity, snapshot, deadline):
                return self._ready(scope_id, cached)
            await cache.check_capacity(deadline=deadline, reserve_bytes=sum(file.size for file in snapshot.files))
            stage = await asyncio.to_thread(cache.stage)
            snapshot = await capture_repository(root, self.config, deadline=deadline, destination=stage)
            result = await cache.bounded(
                self.adapter.build(stage, identity=identity, deadline=deadline), deadline=deadline
            )
            verified = await capture_repository(root, self.config, deadline=deadline)
            if verified != snapshot:
                raise CodeChangedError("code_changed")
            manifest = IndexManifest(
                scope_id=scope_id,
                configuration_digest=self._configuration_digest(scope_id),
                engine_digest=identity.digest,
                index_digest=await asyncio.to_thread(index_digest, stage, deadline=deadline),
                snapshot=snapshot,
                indexed_files=_count(result.get("indexed_files")),
                parse_failures=_count(result.get("parse_failures")),
                unresolved_references=_count(result.get("unresolved_references")),
            )
            await self._verify_engine(identity, deadline)
            await cache.check_capacity(deadline=deadline, reserve_bytes=len(manifest.model_dump_json().encode()) + 128)
            await asyncio.to_thread(cache.publish, stage, manifest)
            return self._ready(scope_id, manifest)

    async def _reusable(
        self,
        cache: CodeCache,
        scope_id: str,
        identity: EngineIdentity,
        snapshot: RepositorySnapshot,
        deadline: float,
    ) -> IndexManifest | None:
        try:
            directory, manifest = await asyncio.to_thread(cache.current)
            self._check_manifest(scope_id, manifest, identity, snapshot)
            await self._verify_cache(directory, manifest, deadline)
        except (CodeChangedError, CodeUnavailableError):
            return None
        return manifest

    async def query(self, scope_id: str, request: CodeQueryRequest) -> CodeQueryResponse | CodeStatusResponse:
        if request.operation.kind == "status":
            response = await self.status(scope_id)
            if len(response.model_dump_json(by_alias=True).encode()) > request.max_bytes:
                raise InvalidCodeRequestError("budget_too_small")
            return response
        return await self._query(scope_id, request, prepare=False)

    async def prepare(self, scope_id: str, query: str) -> CodeQueryResponse:
        request = CodeQueryRequest(
            operation=CodeSymbolsOperation(kind="symbols", query=query, limit=16), max_bytes=32768
        )
        return await self._query(scope_id, request, prepare=True)

    async def status(self, scope_id: str) -> CodeStatusResponse:
        if not self.config.enabled or scope_id not in self.config.repositories:
            return CodeStatusResponse(scope_id=scope_id, status="disabled")
        try:
            if await asyncio.to_thread(self._cache(scope_id).is_building):
                return CodeStatusResponse(scope_id=scope_id, status="building")
            request = CodeQueryRequest(operation=CodeTreeOperation(kind="tree", limit=1))
            result = await self._query(scope_id, request, prepare=False)
        except CodeChangedError:
            return CodeStatusResponse(
                scope_id=scope_id, status="stale", capabilities=_CAPABILITIES, limitations=_LIMITATIONS
            )
        except CodeUnavailableError as error:
            status = "missing" if error.code in {"code_index_missing", "code_engine_missing"} else "failed"
            return CodeStatusResponse(scope_id=scope_id, status=status, limitations=(error.code,))
        except UnsupportedCodeCapabilityError:
            return CodeStatusResponse(scope_id=scope_id, status="failed", limitations=("unsupported_capability",))
        return CodeStatusResponse(
            scope_id=scope_id,
            status="ready",
            fingerprint=result.fingerprint,
            capabilities=_CAPABILITIES,
            limitations=_LIMITATIONS,
        )

    @staticmethod
    def _ready(scope_id: str, manifest: IndexManifest) -> CodeStatusResponse:
        return CodeStatusResponse(
            scope_id=scope_id,
            status="ready",
            fingerprint=manifest.fingerprint(),
            capabilities=_CAPABILITIES,
            limitations=_LIMITATIONS,
        )

    async def _query(self, scope_id: str, request: CodeQueryRequest, *, prepare: bool) -> CodeQueryResponse:
        root = self._repository(scope_id)
        deadline = monotonic() + self.config.limits.query_timeout_seconds
        cache = self._cache(scope_id)
        async with cache.locked(deadline=deadline):
            directory, manifest = await asyncio.to_thread(cache.current)
            identity = await self.adapter.identity(deadline=deadline)
            snapshot = await capture_repository(root, self.config, deadline=deadline)
            self._check_manifest(scope_id, manifest, identity, snapshot)
            fingerprint = manifest.fingerprint()
            if request.expected_fingerprint is not None and request.expected_fingerprint != fingerprint:
                raise CodeChangedError("code_changed")
            await self._verify_cache(directory, manifest, deadline)
            items, truncated, omitted = await self._items(
                directory, manifest, request, cache, identity, deadline, prepare
            )
            verified = await capture_repository(root, self.config, deadline=deadline)
            if verified != snapshot:
                raise CodeChangedError("code_changed")
            await self._verify_engine(identity, deadline)
            coverage = CodeCoverage(
                included_files=len(snapshot.files),
                indexed_files=manifest.indexed_files,
                omitted_files=sum(snapshot.omissions.values()),
                parse_failures=manifest.parse_failures,
                unresolved_references=manifest.unresolved_references,
                truncated=truncated,
            )
            limitations = (*_LIMITATIONS, *(f"{name}: {count}" for name, count in sorted(snapshot.omissions.items())))
            if omitted:
                limitations += (f"Unreliable or unsupported relationships omitted: {omitted}",)
            response = CodeQueryResponse(
                scope_id=scope_id,
                fingerprint=fingerprint,
                commit=snapshot.commit,
                git_object_format=snapshot.git_object_format,
                dirty=snapshot.dirty,
                checked_at=datetime.now(UTC).isoformat(),
                operation=request.operation.kind,
                status="partial",
                items=items,
                coverage=coverage,
                limitations=limitations,
            )
            return fit_response(response, request.max_bytes) if not prepare else response

    def _check_manifest(
        self,
        scope_id: str,
        manifest: IndexManifest,
        identity: EngineIdentity,
        snapshot: RepositorySnapshot,
    ) -> None:
        if (
            manifest.format != 1
            or manifest.scope_id != scope_id
            or manifest.configuration_digest != self._configuration_digest(scope_id)
            or manifest.engine_digest != identity.digest
            or manifest.snapshot != snapshot
        ):
            raise CodeChangedError("code_changed")

    async def _verify_engine(self, identity: EngineIdentity, deadline: float) -> None:
        current = await self.adapter.identity(deadline=deadline)
        if current.digest != identity.digest:
            raise CodeChangedError("code_changed")

    @staticmethod
    async def _verify_cache(directory: Path, manifest: IndexManifest, deadline: float) -> None:
        actual = await asyncio.to_thread(index_digest, directory, deadline=deadline)
        if actual != manifest.index_digest:
            raise CodeUnavailableError("code_cache_invalid")

    async def _items(
        self,
        directory: Path,
        manifest: IndexManifest,
        request: CodeQueryRequest,
        cache: CodeCache,
        identity: EngineIdentity,
        deadline: float,
        prepare: bool,
    ) -> tuple[tuple[CodeItem, ...], bool, int]:
        operation = request.operation
        if isinstance(operation, CodeTreeOperation):
            return _tree(manifest.snapshot, operation)
        if isinstance(operation, CodeReadOperation):
            item = await asyncio.to_thread(_read, directory, manifest.snapshot, operation)
            return (item,), False, 0
        reserve = await asyncio.to_thread(directory_size, directory / ".codegraph", deadline=deadline)
        await cache.check_capacity(deadline=deadline, reserve_bytes=reserve)
        result = await cache.bounded(
            self.adapter.query(
                directory,
                operation,
                identity=identity,
                deadline=deadline,
                max_cache_bytes=self.config.limits.max_cache_bytes,
                prepare=prepare,
            ),
            deadline=deadline,
        )
        try:
            items = tuple(CodeItem.model_validate_json(json.dumps(item)) for item in result["items"])
            hydrated = await asyncio.to_thread(_hydrate, directory, manifest.snapshot, items, prepare)
            return hydrated, result.get("truncated") is True, _count(result.get("omitted"))
        except (KeyError, TypeError, ValueError):
            raise CodeUnavailableError("code_engine_output") from None


def _count(value: object) -> int:
    if type(value) is not int or value < 0:
        raise CodeUnavailableError("code_engine_output")
    return value


def _tree(snapshot: RepositorySnapshot, operation: CodeTreeOperation) -> tuple[tuple[CodeItem, ...], bool, int]:
    prefix = "" if operation.path is None else operation.path + "/"
    entries: dict[str, CodeItem] = {}
    for file in snapshot.files:
        if not file.path.startswith(prefix):
            continue
        parts = file.path[len(prefix) :].split("/")
        for depth in range(1, min(len(parts), operation.depth) + 1):
            path = prefix + "/".join(parts[:depth])
            is_file = depth == len(parts)
            entries[path] = CodeItem(
                kind="file" if is_file else "directory", path=path, file_sha256=file.sha256 if is_file else None
            )
    if operation.path is not None and not entries:
        raise InvalidCodeRequestError("invalid_code_target")
    items = tuple(entries[path] for path in sorted(entries))
    return items[: operation.limit], len(items) > operation.limit, 0


def _file_content(directory: Path, snapshot: RepositorySnapshot, path: str) -> tuple[bytes, str]:
    file = next((file for file in snapshot.files if file.path == path), None)
    if file is None:
        raise InvalidCodeRequestError("invalid_code_target")
    content = read_file_bytes(directory, path, max_bytes=file.size)
    if len(content) != file.size or hashlib.sha256(content).hexdigest() != file.sha256:
        raise CodeUnavailableError("code_cache_invalid")
    return content, file.sha256


def _read(directory: Path, snapshot: RepositorySnapshot, operation: CodeReadOperation) -> CodeItem:
    content, file_sha256 = _file_content(directory, snapshot, operation.path)
    if file_sha256 != operation.file_sha256:
        raise CodeChangedError("code_changed")
    lines = source_lines(content)
    if operation.end_line > len(lines):
        raise InvalidCodeRequestError("invalid_code_line_range")
    selected = b"".join(lines[operation.start_line - 1 : operation.end_line])
    return CodeItem(
        kind="snippet",
        path=operation.path,
        file_sha256=file_sha256,
        location=CodeLocation(path=operation.path, start_line=operation.start_line, end_line=operation.end_line),
        content=selected.decode("utf-8"),
        content_sha256=hashlib.sha256(selected).hexdigest(),
    )


def _hydrate(
    directory: Path,
    snapshot: RepositorySnapshot,
    items: tuple[CodeItem, ...],
    prepare: bool,
) -> tuple[CodeItem, ...]:
    hydrated = []
    for item in items:
        content, file_sha256 = _file_content(directory, snapshot, item.path)
        location = item.location
        if location is None or location.path != item.path:
            raise CodeUnavailableError("code_engine_output")
        lines = source_lines(content)
        if not 1 <= location.start_line <= location.end_line <= len(lines):
            raise CodeUnavailableError("code_engine_output")
        for relationship in item.relationships:
            for endpoint in (relationship.source, relationship.target):
                if endpoint is not None and endpoint.path not in {file.path for file in snapshot.files}:
                    raise CodeUnavailableError("code_engine_output")
        updated = item.model_copy(update={"file_sha256": file_sha256})
        if prepare:
            selected = b"".join(lines[location.start_line - 1 : location.end_line])
            updated = updated.model_copy(
                update={"content": selected.decode(), "content_sha256": hashlib.sha256(selected).hexdigest()}
            )
            updated = clip_item(updated, 2000)
            if updated is None:
                continue
        hydrated.append(updated)
    return tuple(hydrated)
