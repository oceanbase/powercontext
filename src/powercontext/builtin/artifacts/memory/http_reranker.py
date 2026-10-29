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

"""Score Memory candidates through a bounded HTTP reranking service.

The service accepts a limited number of candidates per request, so a full
candidate pool is split into batches while keeping one-based global ranks. All
batch scores then feed a single global ordering, which keeps the final choice
independent of how the pool was split.
"""

from __future__ import annotations

import json
import math

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage

HTTP_RERANK_POLICY_ID = "http-cross-encoder-v1"


class HTTPMemoryReranker:
    """Select Memory hits from scores returned by one HTTP scoring service."""

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = HTTP_RERANK_POLICY_ID,
    ) -> None:
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise ValueError("reranker endpoint must be a non-empty string")  # noqa: TRY003
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
            raise ValueError("reranker batch_size must be a positive integer")  # noqa: TRY003
        if not isinstance(policy_id, str) or not policy_id.strip():
            raise ValueError("reranker policy_id must be a non-empty string")  # noqa: TRY003
        self.endpoint = endpoint
        self.batch_size = batch_size
        self.policy_id = policy_id
        self._client = client

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Score the whole candidate pool in batches and select the global top hits."""

        _validate_bounds(len(candidates), limit)

        scores: dict[int, float] = {}
        requests = 0
        for start in range(0, len(candidates), self.batch_size):
            batch = tuple(range(start + 1, min(start + self.batch_size, len(candidates)) + 1))
            payload = {
                "query": query,
                "candidates": [
                    {"rank": rank, "text": candidates[rank - 1].text} for rank in batch
                ],
            }
            requests += 1
            response = await self._client.post(self.endpoint, json=payload)
            response.raise_for_status()
            scores.update(_parse_scores(response, expected_ranks=batch))

        ranked = sorted(scores, key=lambda rank: (-scores[rank], rank))
        return MemoryRerankDecision(
            selected_ranks=tuple(ranked[:limit]),
            usage=InferenceUsage(requests=requests),
        )


def _validate_bounds(candidate_count: int, limit: int) -> None:
    if candidate_count < 1:
        raise ValueError("rerank requires at least one candidate")  # noqa: TRY003
    if limit < 1 or limit > candidate_count:
        raise ValueError("rerank limit must be between one and the candidate count")  # noqa: TRY003


def _parse_scores(response: httpx.Response, *, expected_ranks: tuple[int, ...]) -> dict[int, float]:
    try:
        payload = json.loads(response.content)
    except ValueError as error:
        raise ValueError("rerank response must be valid JSON") from error
    if not isinstance(payload, dict) or set(payload) != {"scores"}:
        raise ValueError("rerank response must contain exactly the scores field")  # noqa: TRY003
    entries = payload["scores"]
    if not isinstance(entries, list):
        raise ValueError("rerank scores must be a list")  # noqa: TRY003

    expected = set(expected_ranks)
    parsed: dict[int, float] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"rank", "score"}:
            raise ValueError("each rerank score must contain exactly rank and score")  # noqa: TRY003
        rank = entry["rank"]
        score = entry["score"]
        if not isinstance(rank, int) or isinstance(rank, bool) or rank not in expected:
            raise ValueError("rerank score rank must address one candidate of the same request")  # noqa: TRY003
        if rank in parsed:
            raise ValueError("rerank score rank must appear at most once")  # noqa: TRY003
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            raise ValueError("rerank score must be a finite number")  # noqa: TRY003
        value = float(score)
        if not math.isfinite(value):
            raise ValueError("rerank score must be a finite number")  # noqa: TRY003
        parsed[rank] = value
    if set(parsed) != expected:
        raise ValueError("rerank response must cover every candidate of the request exactly once")  # noqa: TRY003
    return parsed


__all__ = ["HTTP_RERANK_POLICY_ID", "HTTPMemoryReranker"]
