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

"""LongMemEval-V2 Memory adapter backed by the public PowerContext HTTP API."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import Path
from typing import Protocol, cast, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

AUDIT_SCHEMA = "powercontext.longmemeval-v2-memory-audit.v1"
DEFAULT_MEMORY_KIND = "longmemeval_v2_trajectory"
DEFAULT_SOURCE_CHUNK_BYTES = 180_000
MAX_SOURCE_CHUNK_BYTES = 190_000
MAX_MEMORY_TEXT_BYTES = 8_192
SEARCH_MODES = ("auto", "fts", "vector", "hybrid")
QUERY_STRATEGIES = ("memory-search", "prepared-context")
PREPARED_CONTEXT_SCHEMA = "powercontext.prepared-context.v1"
MEMORY_PROJECTIONS = ("deterministic-compact-v1", "deterministic-l0-l1-v1")
TASK_LENSES = ("question-keywords-v1",)
_TASK_LENS_STOPWORDS = frozenset(
    {
        "about",
        "and",
        "after",
        "are",
        "before",
        "could",
        "from",
        "for",
        "have",
        "into",
        "should",
        "that",
        "the",
        "their",
        "there",
        "these",
        "they",
        "this",
        "what",
        "when",
        "where",
        "which",
        "with",
        "would",
        "your",
        "you",
    }
)
_HARNESS_RUNTIME_KEYS = {
    "cancel_event",
    "generation_temperature",
    "generation_top_p",
    "query_trace_dir",
}
_AUDIT_LOCKS: dict[Path, threading.Lock] = {}
_AUDIT_LOCKS_GUARD = threading.Lock()


class PowerContextMemoryAdapterError(RuntimeError):
    """The adapter input, transport, or response violated its contract."""


class PowerContextMemoryModeError(PowerContextMemoryAdapterError):
    """The search response did not execute an explicitly required search mode."""


@runtime_checkable
class PowerContextRuntime(Protocol):
    """Narrow synchronous facade over supported public PowerContext operations."""

    def capture_content_source(self, payload: Mapping[str, object]) -> Mapping[str, object]: ...

    def remember_memory(self, payload: Mapping[str, object]) -> Mapping[str, object]: ...

    def search_memory(self, payload: Mapping[str, object]) -> Mapping[str, object]: ...

    def prepare_context(self, payload: Mapping[str, object]) -> Mapping[str, object]: ...


class PowerContextHTTPRuntime:
    """Call the public PowerContext API without depending on package internals."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None,
        timeout_seconds: float,
    ) -> None:
        if not isinstance(base_url, str) or not base_url.strip():
            raise PowerContextMemoryAdapterError("base_url must be a non-empty string")
        _validate_transport(base_url)
        if timeout_seconds <= 0:
            raise PowerContextMemoryAdapterError("timeout_seconds must be positive")
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout_seconds = timeout_seconds

    def capture_content_source(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        return self._post("/v1/sources/content", payload, expected_status=202)

    def remember_memory(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        return self._post("/v1/memory/remember", payload, expected_status=200)

    def search_memory(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        return self._post("/v1/memory/search", payload, expected_status=200)

    def prepare_context(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        return self._post("/v1/context/prepare", payload, expected_status=200)

    def create_scope(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        return self._post("/v1/scopes", payload, expected_status=201)

    def get_readiness(self) -> Mapping[str, object]:
        return self._get("/health/ready", expected_status=200)

    def get_capabilities(self) -> Mapping[str, object]:
        return self._get("/v1/capabilities", expected_status=200)

    def _post(
        self,
        path: str,
        payload: Mapping[str, object],
        *,
        expected_status: int,
    ) -> Mapping[str, object]:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = Request(
            f"{self._base_url}{path}",
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
            headers=headers,
            method="POST",
        )
        return self._request(request, path=path, expected_status=expected_status)

    def _get(self, path: str, *, expected_status: int) -> Mapping[str, object]:
        headers = {} if self._token is None else {"Authorization": f"Bearer {self._token}"}
        request = Request(f"{self._base_url}{path}", headers=headers, method="GET")
        return self._request(request, path=path, expected_status=expected_status)

    def _request(self, request: Request, *, path: str, expected_status: int) -> Mapping[str, object]:
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:
                status = response.status
                body = response.read()
        except HTTPError as error:
            raise PowerContextMemoryAdapterError(f"PowerContext {path} returned HTTP {error.code}") from error
        except (OSError, URLError) as error:
            raise PowerContextMemoryAdapterError(f"PowerContext {path} request failed") from error
        if status != expected_status:
            raise PowerContextMemoryAdapterError(
                f"PowerContext {path} returned HTTP {status}, expected {expected_status}"
            )
        try:
            value = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PowerContextMemoryAdapterError(f"PowerContext {path} returned invalid JSON") from error
        if not isinstance(value, dict):
            raise PowerContextMemoryAdapterError(f"PowerContext {path} returned a non-object response")
        return value


class PowerContextMemory:
    """Duck-typed implementation of the pinned LongMemEval-V2 ``Memory`` contract.

    ``scope_id`` and ``audit_path`` are required memory parameters. Optional
    transport, chunking, and search parameters contain configuration only; the
    bearer token is resolved at runtime through ``token_env`` and is never
    included in ``memory_config`` or audit records.
    """

    memory_type = "powercontext"

    def __init__(self, memory_params: dict[str, object]) -> None:
        self.memory_params = dict(memory_params)
        self.scope_id = _nonblank(self.memory_params.get("scope_id"), "scope_id")
        self.audit_path = Path(_nonblank(self.memory_params.get("audit_path"), "audit_path"))
        self.memory_kind = _optional_nonblank(
            self.memory_params.get("memory_kind"),
            "memory_kind",
            DEFAULT_MEMORY_KIND,
        )
        self.search_mode = _optional_nonblank(self.memory_params.get("search_mode"), "search_mode", "auto")
        if self.search_mode not in SEARCH_MODES:
            raise PowerContextMemoryAdapterError(
                f"search_mode must be one of {', '.join(SEARCH_MODES)}, not {self.search_mode!r}"
            )
        self.query_strategy = _optional_nonblank(
            self.memory_params.get("query_strategy"), "query_strategy", "memory-search"
        )
        if self.query_strategy not in QUERY_STRATEGIES:
            raise PowerContextMemoryAdapterError(
                f"query_strategy must be one of {', '.join(QUERY_STRATEGIES)}, not {self.query_strategy!r}"
            )
        self.prepared_context_max_bytes = _integer(
            self.memory_params.get("prepared_context_max_bytes", 8_000),
            "prepared_context_max_bytes",
            minimum=512,
            maximum=32_768,
        )
        self.memory_projection = _optional_nonblank(
            self.memory_params.get("memory_projection"), "memory_projection", "deterministic-compact-v1"
        )
        if self.memory_projection not in MEMORY_PROJECTIONS:
            raise PowerContextMemoryAdapterError(
                f"memory_projection must be one of {', '.join(MEMORY_PROJECTIONS)}, not {self.memory_projection!r}"
            )
        task_lens = self.memory_params.get("task_lens")
        if task_lens is not None and task_lens not in TASK_LENSES:
            raise PowerContextMemoryAdapterError(f"unsupported task_lens: {task_lens!r}")
        self.task_lens = cast("str | None", task_lens)
        self.search_limit = _integer(self.memory_params.get("search_limit", 10), "search_limit", minimum=1, maximum=50)
        self.source_chunk_bytes = _integer(
            self.memory_params.get("source_chunk_bytes", DEFAULT_SOURCE_CHUNK_BYTES),
            "source_chunk_bytes",
            minimum=512,
            maximum=MAX_SOURCE_CHUNK_BYTES,
        )
        self._runtime: PowerContextRuntime = self._http_runtime()
        self._clock_ns: Callable[[], int] = time.perf_counter_ns
        self._audit_lock = _audit_lock_for(self.audit_path)
        self._insert_lock = threading.Lock()
        self._inserted_trajectory_ids: set[str] = set()
        self._query_context_local = threading.local()

    @property
    def memory_config(self) -> dict[str, object]:
        """Return only reconstructable configuration, never runtime credentials."""

        return {"memory_type": self.memory_type, "memory_params": dict(self.memory_params)}

    def configure_runtime(self, **kwargs: object) -> None:
        """Inject a fake/runtime clock after construction without persisting it."""

        unexpected = set(kwargs) - {"runtime", "clock_ns"} - _HARNESS_RUNTIME_KEYS
        if unexpected:
            raise PowerContextMemoryAdapterError(f"unsupported runtime overrides: {sorted(unexpected)}")
        runtime = kwargs.get("runtime")
        if runtime is not None:
            if not isinstance(runtime, PowerContextRuntime):
                raise PowerContextMemoryAdapterError("runtime does not implement the PowerContext adapter operations")
            self._runtime = runtime
        clock_ns = kwargs.get("clock_ns")
        if clock_ns is not None:
            if not callable(clock_ns):
                raise PowerContextMemoryAdapterError("clock_ns must be callable")
            self._clock_ns = cast("Callable[[], int]", clock_ns)

    def insert(self, trajectory: dict[str, object]) -> None:
        """Capture one projected trajectory as paired Source and Memory chunks."""

        trajectory_id, projected = _project_trajectory(trajectory)
        source_chunks = _utf8_chunks(projected, self.source_chunk_bytes)
        memory_texts = _memory_projection_texts(projected, self.memory_projection)
        digest = f"sha256:{hashlib.sha256(projected.encode()).hexdigest()}"
        started = self._clock_ns()
        capture_ns = 0
        remember_ns = 0
        source_refs: list[dict[str, object]] = []
        memory_citations: list[dict[str, object]] = []
        status = "failed"
        failure: str | None = None
        try:
            with self._insert_lock:
                if trajectory_id in self._inserted_trajectory_ids:
                    raise PowerContextMemoryAdapterError(f"duplicate trajectory insert: {trajectory_id}")
                for index, content in enumerate(source_chunks):
                    source_id = f"longmemeval-v2-{trajectory_id}-{index + 1:04d}"
                    stage_started = self._clock_ns()
                    source = self._runtime.capture_content_source(
                        {
                            "scope_id": self.scope_id,
                            "source_id": source_id,
                            "content": content,
                            "metadata": {
                                "benchmark": "longmemeval-v2",
                                "trajectory_id": trajectory_id,
                                "chunk_index": index,
                                "chunk_count": len(source_chunks),
                                "trajectory_digest": digest,
                            },
                        }
                    )
                    capture_ns += self._clock_ns() - stage_started
                    source_refs.append(_source_ref(source))

                for layer, memory_text in memory_texts:
                    stage_started = self._clock_ns()
                    memory = self._runtime.remember_memory(
                        {
                            "scope_id": self.scope_id,
                            "kind": self.memory_kind,
                            "text": memory_text,
                            "reason": f"LongMemEval-V2 {layer} memory for trajectory {trajectory_id}",
                        }
                    )
                    remember_ns += self._clock_ns() - stage_started
                    memory_citations.append(_memory_citation(memory))
                self._inserted_trajectory_ids.add(trajectory_id)
            status = "succeeded"
        except Exception as error:
            failure = type(error).__name__
            raise
        finally:
            self._write_audit(
                {
                    "operation": "ingest",
                    "status": status,
                    "scope_id": self.scope_id,
                    "trajectory_id": trajectory_id,
                    "trajectory_digest": digest,
                    "memory_projection": self.memory_projection,
                    "source_chunk_count": len(source_chunks),
                    "memory_entry_count": len(memory_citations),
                    "source_refs": source_refs,
                    "memory_citations": memory_citations,
                    "timings_ms": {
                        "source_capture": _milliseconds(capture_ns),
                        "memory_remember": _milliseconds(remember_ns),
                        "total": _milliseconds(self._clock_ns() - started),
                    },
                    "failure_type": failure,
                }
            )

    def query(self, query: str, query_image: str | None = None) -> list[dict[str, str]]:
        """Return Memory search hits as LongMemEval-V2 text context items."""

        question = _nonblank(query, "query")
        retrieval_query = _task_lensed_query(question, self.task_lens)
        if query_image is not None and (not isinstance(query_image, str) or not query_image.strip()):
            raise PowerContextMemoryAdapterError("query_image must be null or a non-empty string")
        started = self._clock_ns()
        search_ns = 0
        format_ns = 0
        citations: list[dict[str, object]] = []
        items: list[dict[str, str]] = []
        actual_mode: str | None = None
        status = "failed"
        failure: str | None = None
        requested_mode = self.search_mode if self.query_strategy == "memory-search" else None
        try:
            stage_started = self._clock_ns()
            if self.query_strategy == "prepared-context":
                response = self._runtime.prepare_context(
                    {
                        "scope_id": self.scope_id,
                        "query": retrieval_query,
                        "max_bytes": self.prepared_context_max_bytes,
                    }
                )
                search_ns = self._clock_ns() - stage_started
                items.extend(_prepared_context_items(response, maximum_bytes=self.prepared_context_max_bytes))
            else:
                response = self._runtime.search_memory(
                    {
                        "scope_id": self.scope_id,
                        "query": retrieval_query,
                        "limit": self.search_limit,
                        "mode": self.search_mode,
                    }
                )
                search_ns = self._clock_ns() - stage_started
                actual_mode = _response_search_mode(response)
                _require_requested_mode(self.search_mode, actual_mode)
                raw_hits = response.get("hits")
                if not isinstance(raw_hits, list):
                    raise PowerContextMemoryAdapterError("search response hits must be an array")
                for index, hit in enumerate(raw_hits):
                    if not isinstance(hit, Mapping):
                        raise PowerContextMemoryAdapterError(f"search hit {index} must be an object")
                    text = hit.get("text")
                    if not isinstance(text, str) or not text:
                        raise PowerContextMemoryAdapterError(f"search hit {index} text must be non-empty")
                    citation = hit.get("citation")
                    if not isinstance(citation, Mapping):
                        raise PowerContextMemoryAdapterError(f"search hit {index} citation must be an object")
                    citations.append(_string_keyed_mapping(citation, f"search hit {index} citation"))
                    items.append({"type": "text", "value": text})
            stage_started = self._clock_ns()
            format_ns = self._clock_ns() - stage_started
            status = "succeeded"
            return items
        except Exception as error:
            failure = type(error).__name__
            raise
        finally:
            query_invocation_id = self.get_query_context().get("query_invocation_id")
            timings = {
                "search": _milliseconds(search_ns),
                "format": _milliseconds(format_ns),
                "total": _milliseconds(self._clock_ns() - started),
            }
            self._query_context_local.last_query_metadata = {
                "query_invocation_id": query_invocation_id,
                "result_count": len(items),
                "retrieval_strategy": self.query_strategy,
                "task_lens": self.task_lens,
                "requested_mode": requested_mode,
                "actual_mode": actual_mode,
                "citations": deepcopy(citations),
                "timings_ms": dict(timings),
            }
            self._write_audit(
                {
                    "operation": "query",
                    "status": status,
                    "scope_id": self.scope_id,
                    "query_invocation_id": query_invocation_id,
                    "query_sha256": hashlib.sha256(question.encode()).hexdigest(),
                    "retrieval_query_sha256": hashlib.sha256(retrieval_query.encode()).hexdigest(),
                    "query_image_present": query_image is not None,
                    "retrieval_strategy": self.query_strategy,
                    "task_lens": self.task_lens,
                    "requested_mode": requested_mode,
                    "actual_mode": actual_mode,
                    "result_count": len(items),
                    "citations": citations,
                    "timings_ms": timings,
                    "failure_type": failure,
                }
            )

    def set_query_context(self, *, query_invocation_id: str) -> None:
        identifier = _nonblank(query_invocation_id, "query_invocation_id")
        self._query_context_local.context = {"query_invocation_id": identifier}

    def clear_query_context(self) -> None:
        if hasattr(self._query_context_local, "context"):
            delattr(self._query_context_local, "context")
        if hasattr(self._query_context_local, "last_query_metadata"):
            delattr(self._query_context_local, "last_query_metadata")
        if hasattr(self._query_context_local, "last_query_metadata"):
            delattr(self._query_context_local, "last_query_metadata")

    def get_query_context(self) -> dict[str, str]:
        context = getattr(self._query_context_local, "context", None)
        return dict(context) if isinstance(context, dict) else {}

    def post_query_hook(
        self,
        *,
        query: str,
        query_image: str | None,
        memory_context: list[dict[str, str]],
    ) -> dict[str, object] | None:
        metadata = getattr(self._query_context_local, "last_query_metadata", None)
        return deepcopy(metadata) if isinstance(metadata, dict) else None

    def _http_runtime(self) -> PowerContextHTTPRuntime:
        base_url = _optional_nonblank(self.memory_params.get("base_url"), "base_url", "http://127.0.0.1:8765")
        token_env = _optional_nonblank(self.memory_params.get("token_env"), "token_env", "POWERCONTEXT_TOKEN")
        timeout = _number(self.memory_params.get("timeout_seconds", 30.0), "timeout_seconds")
        return PowerContextHTTPRuntime(base_url, token=os.getenv(token_env), timeout_seconds=timeout)

    def _write_audit(self, event: dict[str, object]) -> None:
        record = {"schema": AUDIT_SCHEMA, "observed_at": datetime.now(UTC).isoformat(), **event}
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
        with self._audit_lock:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8", newline="") as stream:
                stream.write(line)


def _project_trajectory(trajectory: Mapping[str, object]) -> tuple[str, str]:
    trajectory_id = _nonblank(trajectory.get("id"), "trajectory.id")
    states = trajectory.get("states")
    if not isinstance(states, list) or not states:
        raise PowerContextMemoryAdapterError(f"trajectory {trajectory_id} states must be a non-empty array")
    projected_states: list[dict[str, object]] = []
    for index, raw_state in enumerate(states):
        if not isinstance(raw_state, Mapping):
            raise PowerContextMemoryAdapterError(f"trajectory {trajectory_id} state {index} must be an object")
        projected_states.append(
            {
                "state_index": raw_state.get("state_index", index),
                "step": raw_state.get("step", index),
                "url": raw_state.get("url"),
                "action": raw_state.get("action"),
                "thought": raw_state.get("thought", raw_state.get("thoughts")),
                "accessibility_tree": raw_state.get("accessibility_tree", raw_state.get("text")),
                "screenshot": raw_state.get("screenshot"),
            }
        )
    projected = {
        "id": trajectory_id,
        "domain": trajectory.get("domain"),
        "environment": trajectory.get("environment"),
        "goal": trajectory.get("goal"),
        "outcome": trajectory.get("outcome"),
        "start_url": trajectory.get("start_url"),
        "states": projected_states,
    }
    return trajectory_id, json.dumps(projected, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _utf8_chunks(text: str, maximum_bytes: int) -> list[str]:
    encoded = text.encode()
    chunks: list[str] = []
    offset = 0
    while offset < len(encoded):
        end = min(offset + maximum_bytes, len(encoded))
        while end > offset:
            try:
                chunk = encoded[offset:end].decode()
                break
            except UnicodeDecodeError:
                end -= 1
        if end == offset:
            raise PowerContextMemoryAdapterError("cannot split trajectory into UTF-8 chunks")
        chunks.append(chunk)
        offset = end
    return chunks


def _compact_memory_text(projected: str, *, heading: str = "LongMemEval-V2 deterministic trajectory memory") -> str:
    value = json.loads(projected)
    if not isinstance(value, dict):
        raise PowerContextMemoryAdapterError("projected trajectory must be an object")
    lines = [
        heading,
        f"trajectory_id: {_plain(value.get('id'))}",
        f"domain: {_plain(value.get('domain'))}",
        f"environment: {_plain(value.get('environment'))}",
        f"goal: {_bounded(value.get('goal'), 1_000)}",
        f"outcome: {_plain(value.get('outcome'))}",
        f"start_url: {_bounded(value.get('start_url'), 500)}",
    ]
    states = value.get("states")
    if isinstance(states, list):
        for index, state in enumerate(states):
            if not isinstance(state, Mapping):
                continue
            lines.extend(
                [
                    f"state {index} url: {_bounded(state.get('url'), 300)}",
                    f"state {index} action: {_bounded(state.get('action'), 600)}",
                    f"state {index} thought: {_bounded(state.get('thought'), 600)}",
                    f"state {index} observation: {_bounded(state.get('accessibility_tree'), 600)}",
                ]
            )
    return _truncate_utf8_middle("\n".join(lines), MAX_MEMORY_TEXT_BYTES)


def _memory_projection_texts(projected: str, projection: str) -> list[tuple[str, str]]:
    if projection == "deterministic-compact-v1":
        return [("deterministic compact", _compact_memory_text(projected))]
    if projection != "deterministic-l0-l1-v1":
        raise PowerContextMemoryAdapterError(f"unsupported memory projection: {projection}")
    value = json.loads(projected)
    if not isinstance(value, dict):
        raise PowerContextMemoryAdapterError("projected trajectory must be an object")
    l0 = _truncate_utf8_middle(
        "\n".join(
            [
                "LongMemEval-V2 deterministic L0 trajectory index",
                f"trajectory_id: {_plain(value.get('id'))}",
                f"domain: {_plain(value.get('domain'))}",
                f"environment: {_plain(value.get('environment'))}",
                f"goal: {_bounded(value.get('goal'), 700)}",
                f"outcome: {_plain(value.get('outcome'))}",
            ]
        ),
        1_024,
    )
    # The final heading is built before truncation so the byte limit always applies
    # to the text the Server will actually receive.
    l1 = _compact_memory_text(projected, heading="LongMemEval-V2 deterministic L1 trajectory summary")
    return [("deterministic L0", l0), ("deterministic L1", l1)]


def _task_lensed_query(question: str, task_lens: str | None) -> str:
    if task_lens is None:
        return question
    if task_lens != "question-keywords-v1":
        raise PowerContextMemoryAdapterError(f"unsupported task_lens: {task_lens}")
    keywords: list[str] = []
    seen: set[str] = set()
    for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}", question.lower()):
        if token in _TASK_LENS_STOPWORDS or token in seen:
            continue
        seen.add(token)
        keywords.append(token)
        if len(keywords) == 24:
            break
    return " ".join(keywords) or question


def _plain(value: object) -> str:
    return value if isinstance(value, str) and value else "<none>"


def _bounded(value: object, maximum_chars: int) -> str:
    text = _plain(value).replace("\x00", " ")
    if len(text) <= maximum_chars:
        return text
    head = maximum_chars // 2
    tail = maximum_chars - head
    return f"{text[:head]}…{text[-tail:]}"


def _truncate_utf8_middle(text: str, maximum_bytes: int) -> str:
    encoded = text.encode()
    if len(encoded) <= maximum_bytes:
        return text
    marker = "\n… <middle omitted by deterministic no-model projection> …\n"
    marker_bytes = marker.encode()
    budget = maximum_bytes - len(marker_bytes)
    head = _utf8_prefix(encoded, budget // 2)
    tail = _utf8_suffix(encoded, budget - len(head))
    return head.decode() + marker + tail.decode()


def _utf8_prefix(value: bytes, maximum_bytes: int) -> bytes:
    end = min(len(value), maximum_bytes)
    while end > 0:
        try:
            value[:end].decode()
            return value[:end]
        except UnicodeDecodeError:
            end -= 1
    return b""


def _utf8_suffix(value: bytes, maximum_bytes: int) -> bytes:
    start = max(0, len(value) - maximum_bytes)
    while start < len(value):
        try:
            value[start:].decode()
            return value[start:]
        except UnicodeDecodeError:
            start += 1
    return b""


def _source_ref(response: Mapping[str, object]) -> dict[str, object]:
    value = response.get("source")
    if not isinstance(value, Mapping):
        raise PowerContextMemoryAdapterError("capture response source must be an object")
    return _string_keyed_mapping(value, "capture response source")


def _memory_citation(response: Mapping[str, object]) -> dict[str, object]:
    entry = response.get("entry")
    if not isinstance(entry, Mapping):
        raise PowerContextMemoryAdapterError("remember response entry must be an object")
    citation = entry.get("citation")
    if not isinstance(citation, Mapping):
        raise PowerContextMemoryAdapterError("remember response citation must be an object")
    return _string_keyed_mapping(citation, "remember response citation")


def _response_search_mode(response: Mapping[str, object]) -> str | None:
    """Read the server-reported executed mode; the public contract marks it nullable."""

    value = response.get("mode")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _prepared_context_items(response: Mapping[str, object], *, maximum_bytes: int) -> list[dict[str, str]]:
    """Validate one public PreparedContext response and render upstream text items."""

    if response.get("schema") != PREPARED_CONTEXT_SCHEMA:
        raise PowerContextMemoryAdapterError("prepared context response has an unsupported schema")
    status = response.get("status")
    content = response.get("content")
    content_bytes = response.get("content_bytes")
    if isinstance(content_bytes, bool) or not isinstance(content_bytes, int) or content_bytes < 0:
        raise PowerContextMemoryAdapterError("prepared context content_bytes must be a non-negative integer")
    if content_bytes > maximum_bytes:
        raise PowerContextMemoryAdapterError("prepared context exceeded the requested byte budget")
    if status == "empty":
        if content is not None or content_bytes != 0:
            raise PowerContextMemoryAdapterError("empty prepared context must have null content and zero bytes")
        return []
    if status != "ready" or not isinstance(content, str) or not content:
        raise PowerContextMemoryAdapterError("ready prepared context must contain non-empty text")
    if len(content.encode()) != content_bytes:
        raise PowerContextMemoryAdapterError("prepared context content_bytes does not match its UTF-8 content")
    return [{"type": "text", "value": content}]


def _require_requested_mode(requested_mode: str, actual_mode: str | None) -> None:
    """Fail closed when an explicitly requested search mode was not executed.

    ``auto`` delegates the mode choice to the Server and accepts whatever the Server
    reports. Every explicit mode — the only modes an experiment arm may request — must
    match the reported mode exactly, so an arm's provenance stays provable and two arms
    cannot silently execute the same retrieval path.
    """

    if requested_mode != "auto" and actual_mode != requested_mode:
        raise PowerContextMemoryModeError(
            f"{requested_mode} Memory search was requested but the server reported mode {actual_mode!r}"
        )


def _string_keyed_mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise PowerContextMemoryAdapterError(f"{label} must be an object")
    if not all(isinstance(key, str) for key in value):
        raise PowerContextMemoryAdapterError(f"{label} keys must be strings")
    return {cast(str, key): item for key, item in value.items()}


def _nonblank(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PowerContextMemoryAdapterError(f"{label} must be a non-empty string")
    return value.strip()


def _optional_nonblank(value: object, label: str, default: str) -> str:
    return default if value is None else _nonblank(value, label)


def _integer(value: object, label: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise PowerContextMemoryAdapterError(f"{label} must be an integer from {minimum} through {maximum}")
    return value


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise PowerContextMemoryAdapterError(f"{label} must be positive")
    return float(value)


def _milliseconds(nanoseconds: int) -> float:
    return round(nanoseconds / 1_000_000, 3)


def _audit_lock_for(path: Path) -> threading.Lock:
    identity = path.resolve()
    with _AUDIT_LOCKS_GUARD:
        lock = _AUDIT_LOCKS.get(identity)
        if lock is None:
            lock = threading.Lock()
            _AUDIT_LOCKS[identity] = lock
        return lock


def _validate_transport(base_url: str) -> None:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise PowerContextMemoryAdapterError("base_url must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise PowerContextMemoryAdapterError("base_url must not contain credentials")
    if parsed.query or parsed.fragment:
        raise PowerContextMemoryAdapterError("base_url must not contain query or fragment data")
    if parsed.scheme == "https":
        return
    hostname = parsed.hostname
    try:
        is_loopback = ip_address(hostname).is_loopback
    except ValueError:
        is_loopback = hostname.lower() == "localhost"
    if not is_loopback:
        raise PowerContextMemoryAdapterError("refusing unencrypted non-loopback PowerContext transport")
