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

"""The configured recall embedding budget decides whether a context preparation waits for a slow embedding.

The query embedding of ``prepare_context`` is bounded so a Hook request stays bounded. With a remote provider the
round trip can exceed the default, and retrieval then continues with lexical matching only. The budget is a
runtime setting; these scenarios show it reaching the Atomic Memory search and being reported when it expires.
"""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference import EmbeddingResult, InferenceTimeoutError, InferenceUnavailableError
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, PrepareContextRequest, RuntimeConfig, open_builtin_runtime
from powercontext.builtin.runtime.application import BuiltinRuntime
from powercontext.builtin.scope import ScopeDraft
from powercontext.server.logging import JsonFormatter

_QUERY = "alpha beta gamma"
_MEMORY = "alpha beta gamma memory"
_EVENT = "memory.search.embedding_fallback"
_PROFILE = EmbeddingProfile(
    profile_id="recall-budget", model="deterministic", dimension=2, distance="l2", normalization="unit"
)


class _FailingQueryEmbedding:
    """Embed entries at once but fail the query embedding with ``error``."""

    profile = _PROFILE

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        if any(text == _QUERY for text in texts):
            raise self.error
        return EmbeddingResult(vectors=tuple((0.3, math.sqrt(1.0 - 0.3**2)) for _ in texts))


class _SlowQueryEmbedding:
    """Embed entries at once but hold the query embedding for ``delay`` seconds."""

    profile = _PROFILE

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.query_completed = False

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        if any(text == _QUERY for text in texts):
            await asyncio.sleep(self.delay)
            self.query_completed = True
        similarities = tuple(1.0 if text.endswith(_QUERY) or text == _QUERY else 0.3 for text in texts)
        return EmbeddingResult(vectors=tuple((value, math.sqrt(1.0 - value**2)) for value in similarities))


@asynccontextmanager
async def _runtime(database: Path, budget: float, embedding: Any) -> AsyncIterator[BuiltinRuntime]:
    config = BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"),
        runtime=RuntimeConfig(recall_embedding_timeout_seconds=budget),
    )
    async with open_builtin_runtime(
        config, scheduler_path=database.with_suffix(".scheduler.db"), embedding_model=embedding
    ) as runtime:
        yield runtime


async def _scope_with_memory(runtime: BuiltinRuntime, key: str) -> str:
    assert runtime.scopes is not None
    assert runtime.atomic_memory is not None
    created = await runtime.scopes.create(
        ScopeDraft(title="Budget", summary="Recall embedding budget", idempotency_key=key)
    )
    await runtime.atomic_memory.for_scope(created.scope_id).create((AtomicMemoryContent(kind="fact", text=_MEMORY),))
    return created.scope_id


def _request() -> PrepareContextRequest:
    return PrepareContextRequest.model_validate({
        "query": _QUERY,
        "max_bytes": 8000,
        "assembly": {"sections": [{"family": "memory", "limit": 8}]},
    })


def _fallbacks(caplog: pytest.LogCaptureFixture) -> list[Any]:
    return [record for record in caplog.records if getattr(record, "event", None) == _EVENT]


def _assert_fallback_record(record: Any, error_code: str) -> None:
    """The event classifies the failure and carries no query, exception or provider text."""

    serialized = JsonFormatter().format(record)
    payload = json.loads(serialized)
    assert record.levelname == "WARNING"
    assert record.exc_info is None
    assert payload["event"] == _EVENT
    assert payload["outcome"] == "fallback"
    assert payload["mode"] == "text"
    assert payload["unit"] == "memory"
    assert payload["error_code"] == error_code
    assert "exception" not in payload
    assert _QUERY not in serialized


def test_an_expired_budget_keeps_lexical_recall_and_reports_the_fallback(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    embedding = _SlowQueryEmbedding(delay=0.5)

    async def scenario() -> Any:
        async with _runtime(tmp_path / "expired.db", 0.05, embedding) as runtime:
            scope_id = await _scope_with_memory(runtime, "expired")
            return await runtime.context.for_scope(scope_id).prepare(_request())

    prepared = asyncio.run(scenario())

    assert prepared.status == "ready"
    assert _MEMORY in (prepared.content or "")
    assert embedding.query_completed is False
    (record,) = _fallbacks(caplog)
    _assert_fallback_record(record, "inference_timeout")


@pytest.mark.parametrize(
    ("error", "error_code"),
    [
        (InferenceUnavailableError("embed"), "inference_unavailable"),
        (InferenceTimeoutError("embed", 1.0), "inference_timeout"),
    ],
)
def test_a_failing_provider_keeps_lexical_recall_and_classifies_the_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, error: Exception, error_code: str
) -> None:
    async def scenario() -> Any:
        async with _runtime(tmp_path / "failing.db", 5.0, _FailingQueryEmbedding(error)) as runtime:
            scope_id = await _scope_with_memory(runtime, "failing")
            return await runtime.context.for_scope(scope_id).prepare(_request())

    prepared = asyncio.run(scenario())

    assert prepared.status == "ready"
    assert _MEMORY in (prepared.content or "")
    (record,) = _fallbacks(caplog)
    _assert_fallback_record(record, error_code)


def test_a_sufficient_budget_waits_for_the_query_embedding(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    embedding = _SlowQueryEmbedding(delay=0.3)

    async def scenario() -> Any:
        async with _runtime(tmp_path / "waited.db", 5.0, embedding) as runtime:
            scope_id = await _scope_with_memory(runtime, "waited")
            return await runtime.context.for_scope(scope_id).prepare(_request())

    prepared = asyncio.run(scenario())

    assert prepared.status == "ready"
    assert embedding.query_completed is True
    assert _fallbacks(caplog) == []
