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


class _TimedQueryEmbedding:
    """Embed entries at once and record how long each query embedding is waited for before it is abandoned."""

    profile = _PROFILE

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.waits: list[float] = []

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        if any(text == _QUERY for text in texts):
            started = asyncio.get_running_loop().time()
            try:
                await asyncio.sleep(self.delay)
            finally:
                self.waits.append(asyncio.get_running_loop().time() - started)
        return EmbeddingResult(vectors=tuple((0.3, math.sqrt(1.0 - 0.3**2)) for _ in texts))


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


def _request(*, explain: bool = True) -> PrepareContextRequest:
    """A Hook's request asks for content only; these scenarios also ask why a channel was lost."""

    return PrepareContextRequest.model_validate({
        "query": _QUERY,
        "max_bytes": 8000,
        "assembly": {"sections": [{"family": "memory", "limit": 8}]},
        "explain": explain,
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
    # The status alone cannot tell this apart from "nothing matched": the reason can.
    assert prepared.reason == "inference_timeout"
    (record,) = _fallbacks(caplog)
    _assert_fallback_record(record, "inference_timeout")


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (InferenceUnavailableError("embed"), "inference_unavailable"),
        (InferenceTimeoutError("embed", 1.0), "inference_timeout"),
    ],
)
def test_a_failing_provider_keeps_lexical_recall_and_classifies_the_failure(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
    expected: str,
) -> None:
    """One classification, one spelling: the log ``error_code`` and the response ``reason`` agree.

    RFC 1435 names this vocabulary ``inference_*``, and the fallback event already used it, so the
    response reuses it instead of restating the same cause in the channel's terms.
    """

    async def scenario() -> Any:
        async with _runtime(tmp_path / "failing.db", 5.0, _FailingQueryEmbedding(error)) as runtime:
            scope_id = await _scope_with_memory(runtime, "failing")
            return await runtime.context.for_scope(scope_id).prepare(_request())

    prepared = asyncio.run(scenario())

    assert prepared.status == "ready"
    assert _MEMORY in (prepared.content or "")
    assert prepared.reason == expected
    (record,) = _fallbacks(caplog)
    _assert_fallback_record(record, expected)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (InferenceUnavailableError("embed"), "inference_unavailable"),
        (InferenceTimeoutError("embed", 1.0), "inference_timeout"),
    ],
)
def test_atomic_search_returns_the_logged_embedding_fallback_reason(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, error: Exception, expected: str
) -> None:
    async def scenario() -> Any:
        async with _runtime(tmp_path / "search.db", 5.0, _FailingQueryEmbedding(error)) as runtime:
            scope_id = await _scope_with_memory(runtime, "search-fallback")
            assert runtime.atomic_memory is not None
            return await runtime.atomic_memory.for_scope(scope_id).search(_QUERY, mode="auto")

    page = asyncio.run(scenario())

    assert page.mode == "text"
    assert page.hits
    assert page.embedding_fallback == expected
    (record,) = _fallbacks(caplog)
    _assert_fallback_record(record, expected)


def test_one_budget_bounds_the_embedding_waits_of_a_preparation_across_scopes(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The budget is the preparation's, not each search's.

    A preparation searches its Scope and every context reference. If each search waited a full budget, a slow
    provider would hold a three-Scope preparation for three budgets, past the one-second Hook request of some hosts,
    and the Host would drop the context entirely instead of keeping the lexical matches. Once the first search has
    spent the budget, the later ones do not call the provider, so the fallback event counts only the real timeout.
    """

    budget = 0.2
    embedding = _TimedQueryEmbedding(delay=5.0)

    async def scenario() -> Any:
        async with _runtime(tmp_path / "shared.db", budget, embedding) as runtime:
            assert runtime.scopes is not None
            assert runtime.atomic_memory is not None
            references = tuple([await _scope_with_memory(runtime, f"reference-{index}") for index in range(2)])
            current = await runtime.scopes.create(
                ScopeDraft(
                    title="Current",
                    summary="Two context references",
                    idempotency_key="current",
                    context_references=references,
                )
            )
            await runtime.atomic_memory.for_scope(current.scope_id).create((
                AtomicMemoryContent(kind="fact", text=_MEMORY),
            ))
            return await runtime.context.for_scope(current.scope_id).prepare(_request())

    prepared = asyncio.run(scenario())

    assert prepared.status == "ready"
    assert _MEMORY in (prepared.content or "")
    assert prepared.reason == "inference_timeout"
    # This is the external inference-attempt budget for one request.
    assert len(embedding.waits) == 1
    assert embedding.waits[0] < budget * 1.5
    (record,) = _fallbacks(caplog)
    _assert_fallback_record(record, "inference_timeout")


def test_a_sufficient_budget_waits_for_the_query_embedding(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    embedding = _SlowQueryEmbedding(delay=0.3)

    async def scenario() -> Any:
        async with _runtime(tmp_path / "waited.db", 5.0, embedding) as runtime:
            scope_id = await _scope_with_memory(runtime, "waited")
            return await runtime.context.for_scope(scope_id).prepare(_request())

    prepared = asyncio.run(scenario())

    assert prepared.status == "ready"
    assert embedding.query_completed is True
    assert prepared.reason is None
    assert _fallbacks(caplog) == []


def test_a_preparation_that_did_not_ask_says_nothing_about_the_lost_channel(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """``reason`` is opt-in: the envelope a Host injects keeps the four fields it validates.

    The cause is still logged, so an operator keeps the signal on a Host that never opts in.
    """

    async def scenario() -> Any:
        async with _runtime(tmp_path / "unexplained.db", 0.05, _SlowQueryEmbedding(delay=0.5)) as runtime:
            scope_id = await _scope_with_memory(runtime, "unexplained")
            return await runtime.context.for_scope(scope_id).prepare(_request(explain=False))

    prepared = asyncio.run(scenario())

    assert prepared.status == "ready"
    assert _MEMORY in (prepared.content or "")
    assert prepared.reason is None
    (record,) = _fallbacks(caplog)
    _assert_fallback_record(record, "inference_timeout")


def test_a_deployment_without_an_embedding_model_is_not_reported_as_degraded(tmp_path: Path) -> None:
    """Lexical-only is a deployment choice, not a lost channel.

    Filling ``reason`` here would make every preparation on a keyword-only install look
    broken, which is exactly the signal this field exists to give.
    """

    async def scenario() -> Any:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'lexical.db'}"))
        async with open_builtin_runtime(
            config, scheduler_path=tmp_path.with_suffix(".scheduler.db"), embedding_model=None
        ) as runtime:
            scope_id = await _scope_with_memory(runtime, "lexical")
            return await runtime.context.for_scope(scope_id).prepare(_request())

    prepared = asyncio.run(scenario())

    assert prepared.status == "ready"
    assert prepared.reason is None
