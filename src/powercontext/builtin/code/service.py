# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Native repository indexing and read-only code evidence service."""

from __future__ import annotations

import shutil
import sqlite3
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from powercontext.builtin.code.cache import Generation, GenerationCache, file_lock, hash_file, read_json, read_private
from powercontext.builtin.code.capture import (
    Capture,
    capture_repository,
    check_deadline,
    compatible_baseline,
    digest_bytes,
    json_bytes,
    source_lines,
    write_private,
)
from powercontext.builtin.code.errors import CodeError
from powercontext.builtin.code.extract import extraction_key
from powercontext.builtin.code.graph import GraphStore
from powercontext.builtin.code.languages import LANGUAGES, parser_builds
from powercontext.builtin.code.models import (
    CodeConfig,
    CodeQueryRequest,
    CodeQueryResult,
    CodeRepositoryConfig,
    CodeStatus,
    TestsOperation,
)
from powercontext.builtin.code.process import extract_jobs
from powercontext.builtin.code.query import GraphQuery
from powercontext.builtin.code.resolve_polyglot import RESOLVER_BUILDS, resolve_facts
from powercontext.builtin.code.store import SCHEMA_VERSION, SQLiteGraphStore
from powercontext.builtin.code.telemetry import observed, stage


def _now() -> str:
    return datetime.now(UTC).isoformat()


class CodeService:
    """Own a local rebuildable index; callers must authorize the selected Scope."""

    def __init__(self, config: CodeConfig, *, store: GraphStore | None = None) -> None:
        self.config = config
        self.store = store if store is not None else SQLiteGraphStore()

    def _binding(self, scope_id: str) -> tuple[CodeRepositoryConfig, GenerationCache, str]:
        if not self.config.enabled:
            raise CodeError("code_disabled")
        repository = self.config.repositories.get(scope_id)
        if repository is None:
            raise CodeError("code_repository_unconfigured")
        root = repository.root.resolve()
        cache_root = self.config.cache_dir.resolve()
        if cache_root == root or root in cache_root.parents:
            raise CodeError("code_cache_inside_repository", status=422)
        identity = digest_bytes(json_bytes([scope_id, str(root), repository.model_dump(mode="json")]))
        if self.store.identity != "sqlite":
            identity = digest_bytes(json_bytes([identity, self.store.identity]))
        cache = GenerationCache(cache_root / identity, self.store)
        return repository, cache, identity

    @observed("index")
    def index(self, scope_id: str, *, full: bool = False) -> dict[str, Any]:
        """Synchronously build and atomically publish a verified generation."""
        repository, cache, binding = self._binding(scope_id)
        deadline = time.monotonic() + self.config.limits.build_seconds
        cache.initialize()
        with file_lock(cache.directory / "build.lock", deadline):
            started = time.monotonic()
            cache.replace_json("last-build.json", {"status": "building", "started_at": _now()})
            # A previous interrupted builder cannot retain an active generation here.
            for abandoned in cache.directory.glob("staging-*"):
                if abandoned.is_dir() and not abandoned.is_symlink():
                    self.store.remove(abandoned, deadline)
                    shutil.rmtree(abandoned)
            try:
                result = self._build(repository, cache, binding, deadline, full=full)
                result.update(status="ready", elapsed_seconds=time.monotonic() - started, finished_at=_now())
                cache.replace_json("last-build.json", result)
            except CodeError as error:
                cache.replace_json("last-build.json", {"status": "failed", "reason": error.code, "finished_at": _now()})
                raise
            except (OSError, ValueError, KeyError) as error:
                cache.replace_json(
                    "last-build.json", {"status": "failed", "reason": "code_build_failed", "finished_at": _now()}
                )
                raise CodeError("code_build_failed") from error
            else:
                return result

    @observed("sync")
    def sync(self, scope_id: str) -> dict[str, Any]:
        return self.index(scope_id)

    @observed("clear")
    def clear(self, scope_id: str) -> dict[str, Any]:
        """Unpublish this configured binding and collect generations without active readers."""
        _, cache, _ = self._binding(scope_id)
        return cache.clear(time.monotonic() + self.config.limits.build_seconds)

    def _build(
        self,
        repository: CodeRepositoryConfig,
        cache: GenerationCache,
        binding: str,
        deadline: float,
        *,
        full: bool,
    ) -> dict[str, Any]:
        previous = None
        try:
            pointer = cache.pointer()
            if pointer:
                previous = cache.load(pointer["current"], deadline)
        except (CodeError, OSError, KeyError, TypeError):
            previous = None
        for attempt in range(2):
            staging = cache.staging()
            try:
                with stage("capture"):
                    captured = capture_repository(
                        repository, self.config.limits, deadline, content_dir=staging / "source"
                    )
                fingerprint = self._fingerprint(binding, repository, captured)
                if previous and previous.fingerprint == fingerprint and not full and self._intact(previous, deadline):
                    # Verify again even when extraction can be reused wholesale.
                    self._verify(repository, previous, deadline)
                    return {
                        "fingerprint": fingerprint,
                        "extracted_files": 0,
                        "reused_files": len(previous.manifest["included_files"]),
                    }
                facts, entries, extracted = self._facts(staging, captured, previous, deadline, full=full)
                with stage("resolve") as attributes:
                    edges, diagnostics = resolve_facts(facts, repository.source_roots, deadline)
                    attributes.update(edge_count=len(edges), diagnostic_count=sum(map(len, diagnostics.values())))
                nodes = [node for fact in facts for node in fact["nodes"]]
                with stage("store"):
                    storage = self.store.create(staging, nodes, edges, deadline)
                for path, issues in diagnostics.items():
                    key = entries[path]["extraction_key"]
                    content = json_bytes({"items": issues})
                    write_private(staging / "diagnostics" / f"{key}.json", content)
                    entries[path]["diagnostics_sha256"] = digest_bytes(content)
                with stage("verify"):
                    after = capture_repository(repository, self.config.limits, deadline)
                if captured.content_identity != after.content_identity:
                    if attempt == 0:
                        continue
                    raise CodeError("workspace_busy", status=409)
                manifest = self._manifest(binding, captured, fingerprint, entries, facts, edges, diagnostics)
                manifest.update(storage)
                manifest["previous_fingerprint"] = previous.fingerprint if previous else None
                with stage("publish") as attributes:
                    generation = cache.publish(staging, manifest, deadline, self.config.limits.max_cache_bytes)
                    attributes.update(manifest["coverage"])
                    attributes.update(extracted_files=extracted, reused_files=len(entries) - extracted)
                return {
                    "fingerprint": generation.fingerprint,
                    "extracted_files": extracted,
                    "reused_files": len(entries) - extracted,
                }
            finally:
                if staging.exists():
                    self.store.remove(staging, time.monotonic() + self.config.limits.query_seconds)
                    shutil.rmtree(staging)
        raise CodeError("workspace_busy", status=409)

    @staticmethod
    def _intact(generation: Generation, deadline: float) -> bool:
        try:
            for path, record in generation.manifest["included_files"].items():
                generation.source(path, deadline)
                for directory, field in (("facts", "facts_sha256"), ("diagnostics", "diagnostics_sha256")):
                    filename = generation.directory / directory / f"{record['extraction_key']}.json"
                    if digest_bytes(read_private(filename)) != record[field]:
                        return False
        except (CodeError, KeyError):
            return False
        return True

    def _fingerprint(self, binding: str, repository: CodeRepositoryConfig, capture: Capture) -> str:
        return digest_bytes(
            json_bytes({
                "binding": binding,
                "capture": capture.identity(),
                "repository_policy": repository.model_dump(mode="json"),
                "parser": parser_builds(),
                "resolver": RESOLVER_BUILDS,
                "schema": SCHEMA_VERSION,
            })
        )

    @observed("extract")
    def _facts(
        self,
        staging: Path,
        captured: Capture,
        previous: Generation | None,
        deadline: float,
        *,
        full: bool,
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], int]:
        entries: dict[str, dict[str, Any]] = {}
        jobs = []
        for item in captured.files:
            if item.reason is not None or item.sha256 is None:
                continue
            key = extraction_key(item.path, item.sha256, item.language)
            record = {**asdict(item), "extraction_key": key}
            entries[item.path] = record
            old = previous.manifest["included_files"].get(item.path) if previous and not full else None
            reused = self._reuse_facts(previous, old, key, staging)
            if not reused:
                jobs.append(record)
        if jobs:
            self._extract_jobs(staging, jobs, deadline)
        facts = []
        for path, entry in entries.items():
            check_deadline(deadline)
            fact_file = staging / "facts" / f"{entry['extraction_key']}.json"
            entry["facts_sha256"] = hash_file(fact_file, deadline)
            fact = read_json(fact_file)
            if fact["path"] != path or fact["file_sha256"] != entry["sha256"]:
                raise CodeError("index_integrity_failed")
            entry["parse_status"] = fact["nodes"][0]["parse_status"]
            facts.append(fact)
        return facts, entries, len(jobs)

    @staticmethod
    def _reuse_facts(previous: Generation | None, old: dict[str, Any] | None, key: str, staging: Path) -> bool:
        if previous is None or old is None or old["extraction_key"] != key:
            return False
        try:
            content = read_private(previous.directory / "facts" / f"{key}.json")
        except CodeError:
            return False
        if digest_bytes(content) != old["facts_sha256"]:
            return False
        write_private(staging / "facts" / f"{key}.json", content)
        return True

    def _extract_jobs(self, staging: Path, jobs: list[dict[str, Any]], deadline: float) -> None:
        extract_jobs(
            staging,
            jobs,
            deadline,
            memory_bytes=self.config.limits.worker_memory_bytes,
            parse_seconds=self.config.limits.parse_seconds,
        )

    def _manifest(
        self,
        binding: str,
        capture: Capture,
        fingerprint: str,
        entries: dict[str, dict[str, Any]],
        facts: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        diagnostics: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        coverage = {
            "eligible_files": len(entries),
            "parsed_files": sum(
                entry["language"] in LANGUAGES and entry["parse_status"] == "ok" for entry in entries.values()
            ),
            "partial_files": sum(entry["parse_status"] == "partial" for entry in entries.values()),
            "failed_files": sum(entry["parse_status"] == "failed" for entry in entries.values()),
            "unsupported_files": sum(entry["language"] not in LANGUAGES for entry in entries.values()),
            "skipped_files": len(capture.files) - len(entries) + capture.invalid_paths,
            "references": sum(len(fact["references"]) for fact in facts),
            "resolved_references": len({
                edge["reference_key"]
                for edge in edges
                if edge["reference_key"] and edge["resolution"] == "resolved_static"
            }),
            "candidate_references": len({
                edge["reference_key"] for edge in edges if edge["reference_key"] and edge["resolution"] == "candidate"
            }),
            "unresolved_references": sum(not item["candidates"] for issues in diagnostics.values() for item in issues),
        }
        coverage["languages"] = {
            language: {
                "files": sum(entry["language"] == language for entry in entries.values()),
                **{
                    status: sum(
                        entry["language"] == language and entry["parse_status"] == status for entry in entries.values()
                    )
                    for status in ("ok", "partial", "failed")
                },
            }
            for language in LANGUAGES
        }
        return {
            "schema": SCHEMA_VERSION,
            "binding_id": binding,
            "fingerprint": fingerprint,
            "capture_identity": capture.content_identity,
            "capture": capture.identity(),
            "commit": capture.commit,
            "git_object_format": capture.object_format,
            "dirty": capture.dirty,
            "parser_build": parser_builds(),
            "resolver_build": RESOLVER_BUILDS,
            "included_files": entries,
            "coverage": coverage,
            "created_at": _now(),
        }

    @observed("verify")
    def _verify(self, repository: CodeRepositoryConfig, generation: Generation, deadline: float) -> None:
        captured = capture_repository(repository, self.config.limits, deadline)
        if captured.content_identity != generation.manifest["capture_identity"]:
            raise CodeError("code_changed", status=409)
        if (
            generation.manifest["parser_build"] != parser_builds()
            or generation.manifest["resolver_build"] != RESOLVER_BUILDS
        ):
            raise CodeError("code_changed", status=409)

    @observed("status")
    def status(self, scope_id: str) -> CodeStatus:
        try:
            repository, cache, _ = self._binding(scope_id)
        except CodeError as error:
            if error.code in {"code_disabled", "code_repository_unconfigured"}:
                return CodeStatus(scope_id=scope_id, status="disabled", reason=error.code)
            raise
        deadline = time.monotonic() + self.config.limits.query_seconds
        last_build = None
        fingerprint = None
        try:
            if (cache.directory / "last-build.json").exists():
                last_build = read_json(cache.directory / "last-build.json")
            with cache.pin(deadline) as (generation, _):
                fingerprint = generation.fingerprint
                self._verify(repository, generation, deadline)
                return CodeStatus(
                    scope_id=scope_id,
                    status="ready",
                    freshness="fresh",
                    fingerprint=generation.fingerprint,
                    last_build=last_build,
                )
        except (CodeError, OSError, KeyError, TypeError) as error:
            reason = error.code if isinstance(error, CodeError) else "index_integrity_failed"
            state = "stale" if reason == "code_changed" else "missing" if reason == "code_index_missing" else "failed"
            if state == "missing" and last_build and last_build.get("status") == "building":
                state = "building"
            return CodeStatus(
                scope_id=scope_id,
                status=state,
                freshness="stale" if state == "stale" else "unknown",
                fingerprint=fingerprint,
                last_build=last_build,
                reason=reason,
            )

    def query(self, scope_id: str, request: CodeQueryRequest) -> CodeQueryResult | CodeStatus:
        with stage("query") as attributes:
            attributes["operation"] = request.operation.kind
            try:
                result = self._query(scope_id, request)
            except (OSError, KeyError, TypeError, sqlite3.DatabaseError) as error:
                raise CodeError("index_integrity_failed") from error
            attributes["status"] = result.status
            if isinstance(result, CodeQueryResult):
                attributes.update({key: value for key, value in result.coverage.items() if isinstance(value, int)})
                attributes["selected_count"] = len(result.items)
                attributes["limitations"] = ",".join(result.limitations)
            elif result.reason:
                attributes["reason"] = result.reason
            return result

    def _query(self, scope_id: str, request: CodeQueryRequest) -> CodeQueryResult | CodeStatus:
        if request.operation.kind == "status":
            result = self.status(scope_id)
            if len(json_bytes(result.model_dump(mode="json", by_alias=True))) > request.max_bytes:
                raise CodeError("budget_too_small", status=422)
            return result
        repository, cache, _ = self._binding(scope_id)
        deadline = time.monotonic() + self.config.limits.query_seconds
        with cache.pin(deadline, previous=request.operation.kind in {"changes", "impact_changes"}) as (
            generation,
            before,
        ):
            self._verify(repository, generation, deadline)
            if request.expected_fingerprint and request.expected_fingerprint != generation.fingerprint:
                raise CodeError("code_changed", status=409)
            if before is not None and not compatible_baseline(
                repository.root, before.manifest["capture"], generation.manifest["capture"], deadline
            ):
                raise CodeError("baseline_unavailable", status=409)
            engine = GraphQuery(generation, deadline)
            items = self._change_query(request, engine, before) if before is not None else engine.execute(request)
            self._verify(repository, generation, deadline)
            result = CodeQueryResult(
                scope_id=scope_id,
                fingerprint=generation.fingerprint,
                before_fingerprint=before.fingerprint if before else None,
                commit=generation.manifest["commit"],
                git_object_format=generation.manifest["git_object_format"],
                dirty=generation.manifest["dirty"],
                checked_at=_now(),
                operation=request.operation.kind,
                status="partial" if engine.truncated else "ok",
                items=items,
                coverage={**generation.manifest["coverage"], "path_boundary_edges": engine.boundary_edges},
                limitations=sorted(engine.limitations),
            )
            return self._budget(result, request.max_bytes, deadline)

    def _change_query(self, request: CodeQueryRequest, engine: GraphQuery, before: Generation) -> list[dict[str, Any]]:
        previous_files = before.manifest["included_files"]
        current_files = engine.generation.manifest["included_files"]
        changes = []
        for path in sorted(previous_files.keys() | current_files.keys()):
            check_deadline(engine.deadline)
            old, new = previous_files.get(path), current_files.get(path)
            if old and new and old["sha256"] == new["sha256"] and old["mode"] == new["mode"]:
                continue
            changes.append({
                "path": path,
                "change": "added" if old is None else "deleted" if new is None else "modified",
                "before_sha256": old["sha256"] if old else None,
                "after_sha256": new["sha256"] if new else None,
            })
        if request.operation.kind == "changes":
            return changes
        if request.before_fingerprint != before.fingerprint:
            raise CodeError("baseline_unavailable", status=409)
        operation = request.operation
        if not isinstance(operation, TestsOperation):
            raise CodeError("unsupported_capability", status=501)
        if not set(operation.paths).issubset({item["path"] for item in changes}):
            raise CodeError("code_target_missing", status=422)
        items = []
        boundary_edges = 0
        for label, graph, files in (
            ("before", GraphQuery(before, engine.deadline), previous_files),
            ("after", engine, current_files),
        ):
            paths = tuple(path for path in operation.paths if path in files)
            if not paths:
                continue
            with graph.generation.graph(graph.deadline) as connection:
                seeds = graph.path_seeds(connection, paths)
                affected = graph.walk(
                    connection, seeds, operation.path_prefix, 5, reverse=True, impact=True, maximum=operation.limit
                )
                for item in affected:
                    item["generation"] = label
                    items.append(item)
            engine.limitations.update(graph.limitations)
            engine.truncated |= graph.truncated
            boundary_edges += graph.boundary_edges
        engine.boundary_edges = boundary_edges
        return items

    @staticmethod
    @observed("render_result")
    def _budget(result: CodeQueryResult, maximum: int, deadline: float) -> CodeQueryResult:
        check_deadline(deadline)
        data = result.model_dump(mode="json", by_alias=True)
        items, data["items"] = data["items"], []
        sizes = []
        for item in items:
            check_deadline(deadline)
            sizes.append(len(json_bytes(item)))
        size = len(json_bytes(data))
        if size + sum(sizes) + max(0, len(items) - 1) <= maximum:
            check_deadline(deadline)
            return result
        data["status"] = "partial"
        if "output_budget" not in data["limitations"]:
            data["limitations"].append("output_budget")
        size = len(json_bytes(data))
        if size > maximum:
            raise CodeError("budget_too_small", status=422)
        for item, item_size in zip(items, sizes, strict=True):
            check_deadline(deadline)
            separator = int(bool(data["items"]))
            available = maximum - size - separator
            if item_size <= available:
                data["items"].append(item)
                size += item_size + separator
                continue
            fitted = CodeService._fit_source(item, available, deadline)
            if fitted is not None:
                data["items"].append(fitted)
            break
        fitted_result = CodeQueryResult.model_validate(data)
        check_deadline(deadline)
        return fitted_result

    @staticmethod
    def _fit_source(item: dict[str, Any], maximum: int, deadline: float) -> dict[str, Any] | None:
        content = item.get("content")
        lines = source_lines(content) if isinstance(content, str) else []
        low, high = 1, len(lines) - 1
        fitted = None
        while low <= high:
            check_deadline(deadline)
            count = (low + high) // 2
            reduced = "".join(lines[:count])
            candidate = {
                **item,
                "content": reduced,
                "end_line": item["start_line"] + count - 1,
                "snippet_sha256": digest_bytes(reduced.encode()),
            }
            if len(json_bytes(candidate)) <= maximum:
                fitted = candidate
                low = count + 1
            else:
                high = count - 1
        return fitted
