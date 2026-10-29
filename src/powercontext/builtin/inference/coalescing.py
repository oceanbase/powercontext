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

"""Share and cache Embedding work across concurrent Memory callers.

Each text is embedded at most once while a request is in flight, and successful
vectors are retained in a bounded LRU. Waiters keep their own input order and
duplicates, receive only the usage of the batches they actually waited for, and
can be cancelled without cancelling work other callers still depend on.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import count
from typing import Any, Protocol

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage


class _BaseEmbeddingModel(Protocol):
    profile: Any

    async def embed(self, texts: Sequence[str], /) -> EmbeddingResult: ...


@dataclass(frozen=True, slots=True)
class _Settled:
    """One resolved vector plus the identity and billing of its batch."""

    batch_id: int
    vector: EmbeddingVector
    usage: InferenceUsage


class CoalescingEmbeddingModel:
    """Deduplicate, batch and cache Embedding calls made on one event loop."""

    def __init__(
        self,
        model: _BaseEmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if not isinstance(max_batch_size, int) or isinstance(max_batch_size, bool) or max_batch_size < 1:
            raise ValueError("max_batch_size must be a positive integer")  # noqa: TRY003
        if isinstance(coalesce_ms, bool) or not isinstance(coalesce_ms, (int, float)) or coalesce_ms < 0:
            raise ValueError("coalesce_ms must be a non-negative number")  # noqa: TRY003
        if not isinstance(cache_size, int) or isinstance(cache_size, bool) or cache_size < 0:
            raise ValueError("cache_size must be a non-negative integer")  # noqa: TRY003
        self._model = model
        self._max_batch_size = max_batch_size
        self._coalesce_seconds = float(coalesce_ms) / 1000.0
        self._cache_size = cache_size
        self._cache: OrderedDict[str, EmbeddingVector] = OrderedDict()
        self._inflight: dict[str, asyncio.Future[_Settled]] = {}
        self._pending: list[str] = []
        self._batches = count(1)
        self._dispatcher: asyncio.Task[None] | None = None

    @property
    def profile(self) -> Any:
        """Expose the wrapped model's Embedding profile unchanged."""

        return self._model.profile

    @property
    def model(self) -> _BaseEmbeddingModel:
        return self._model

    async def embed(self, texts: Sequence[str], /) -> EmbeddingResult:
        """Return one vector per input position, sharing work with concurrent callers."""

        try:
            requested = tuple(texts)
        except TypeError as error:
            raise ValueError("embedding input must be a sequence of texts") from error
        if not requested:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))

        unique: list[str] = []
        seen: set[str] = set()
        for text in requested:
            if not isinstance(text, str):
                raise ValueError("embedding input must contain text")  # noqa: TRY003
            if text not in seen:
                seen.add(text)
                unique.append(text)

        cache_hits: dict[str, EmbeddingVector] = {}
        waiters: dict[str, asyncio.Future[_Settled]] = {}
        for text in unique:
            cached = self._cache_get(text)
            if cached is not None:
                cache_hits[text] = cached
                continue
            future = self._inflight.get(text)
            if future is None:
                future = asyncio.get_running_loop().create_future()
                self._inflight[text] = future
                self._pending.append(text)
                self._schedule()
            waiters[text] = future

        vectors: dict[str, EmbeddingVector] = dict(cache_hits)
        usages: dict[int, InferenceUsage] = {}
        for text, future in waiters.items():
            settled = await asyncio.shield(future)
            vectors[text] = settled.vector
            usages.setdefault(settled.batch_id, settled.usage)

        return EmbeddingResult(
            vectors=tuple(vectors[text] for text in requested),
            usage=_sum_usage(usages.values()),
        )

    def _cache_get(self, text: str) -> EmbeddingVector | None:
        if self._cache_size == 0:
            return None
        vector = self._cache.get(text)
        if vector is not None:
            self._cache.move_to_end(text)
        return vector

    def _cache_put(self, text: str, vector: EmbeddingVector) -> None:
        if self._cache_size == 0:
            return
        self._cache[text] = vector
        self._cache.move_to_end(text)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    def _schedule(self) -> None:
        if self._dispatcher is None or self._dispatcher.done():
            self._dispatcher = asyncio.get_running_loop().create_task(self._dispatch())

    async def _dispatch(self) -> None:
        if self._coalesce_seconds > 0:
            await asyncio.sleep(self._coalesce_seconds)
        else:
            # Still yield once so same-tick callers join the same window.
            await asyncio.sleep(0)
        while self._pending:
            size = min(self._max_batch_size, len(self._pending))
            batch = self._pending[:size]
            del self._pending[:size]
            # Independent batches run as their own tasks: one slow or cancelled
            # waiter must not delay or abort the rest.
            asyncio.get_running_loop().create_task(self._run_batch(batch))

    async def _run_batch(self, texts: list[str]) -> None:
        batch_id = next(self._batches)
        try:
            result = await self._model.embed(tuple(texts))
        except asyncio.CancelledError:
            for text in texts:
                future = self._inflight.pop(text, None)
                if future is not None and not future.done():
                    future.cancel()
            raise
        except BaseException as error:  # noqa: BLE001 - forwarded to every waiter of this batch
            for text in texts:
                future = self._inflight.pop(text, None)
                if future is not None and not future.done():
                    future.set_exception(error)
            return

        vectors = tuple(result.vectors)
        if len(vectors) != len(texts):
            error = ValueError(
                f"embedding provider returned {len(vectors)} vectors for {len(texts)} texts"
            )
            for text in texts:
                future = self._inflight.pop(text, None)
                if future is not None and not future.done():
                    future.set_exception(error)
            return

        for text, vector in zip(texts, vectors, strict=True):
            future = self._inflight.pop(text, None)
            self._cache_put(text, vector)
            if future is not None and not future.done():
                future.set_result(_Settled(batch_id=batch_id, vector=vector, usage=result.usage))


def _sum_usage(usages: Sequence[InferenceUsage]) -> InferenceUsage:
    requests = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    for usage in usages:
        requests += usage.requests
        if usage.input_tokens is not None:
            input_tokens = usage.input_tokens if input_tokens is None else input_tokens + usage.input_tokens
        if usage.output_tokens is not None:
            output_tokens = usage.output_tokens if output_tokens is None else output_tokens + usage.output_tokens
    return InferenceUsage(requests=requests, input_tokens=input_tokens, output_tokens=output_tokens)


__all__ = ["CoalescingEmbeddingModel"]
