from __future__ import annotations

import httpx
import json
from typing import Sequence
from dataclasses import dataclass

from powercontext.artifacts.memory import MemoryReranker, MemoryRerankDecision
from powercontext.core import InferenceUsage


@dataclass
class _CandidateWithRank:
    rank: int
    text: str


class HTTPMemoryReranker(MemoryReranker):
    def __init__(
        self,
        endpoint: str,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = "http-cross-encoder-v1"
    ):
        if batch_size <= 0:
            raise ValueError("batch_size must > 0")
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size
        self._policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: Sequence[str],
        limit: int
    ) -> MemoryRerankDecision:
        if not candidates:
            raise ValueError("candidates cannot be empty")
        if not (1 <= limit <= len(candidates)):
            raise ValueError("limit out of bounds")

        ranked_candidates: list[_CandidateWithRank] = [
            _CandidateWithRank(rank=i + 1, text=txt)
            for i, txt in enumerate(candidates)
        ]
        rank2score: dict[int, float] = {}
        request_count = 0

        for batch_start in range(0, len(ranked_candidates), self._batch_size):
            batch_slice = ranked_candidates[batch_start: batch_start + self._batch_size]
            req_payload = {
                "query": query,
                "candidates": [
                    {"rank": item.rank, "text": item.text}
                    for item in batch_slice
                ]
            }
            try:
                resp = await self._client.post(
                    self._endpoint,
                    json=req_payload,
                    timeout=None
                )
                request_count += 1
                if resp.status_code < 200 or resp.status_code >= 300:
                    raise ValueError(f"HTTP rerank service status {resp.status_code}")
                resp_data = resp.json()
            except httpx.HTTPError:
                raise ValueError("HTTP request failed")
            except json.JSONDecodeError:
                raise ValueError("response is invalid json")

            if "scores" not in resp_data:
                raise ValueError("missing scores field in response")
            batch_rank_set = {c.rank for c in batch_slice}
            resp_rank_set = set()
            for score_item in resp_data["scores"]:
                r = score_item.get("rank")
                s = score_item.get("score")
                if r is None or s is None:
                    raise ValueError("score item missing rank or score")
                if not isinstance(s, (int, float)):
                    raise ValueError("score must be numeric")
                if isinstance(s, float) and (not (float("-inf") < s < float("inf"))):
                    raise ValueError("score cannot be NaN / Inf")
                if r in resp_rank_set:
                    raise ValueError(f"duplicate rank {r} in batch response")
                resp_rank_set.add(r)
                rank2score[r] = float(s)
            if resp_rank_set != batch_rank_set:
                raise ValueError("response ranks mismatch batch candidates")

        sorted_all = sorted(
            ranked_candidates,
            key=lambda x: (-rank2score[x.rank], x.rank)
        )
        selected = [item.text for item in sorted_all[:limit]]
        rejected = [item.text for item in sorted_all[limit:]]

        usage = InferenceUsage(requests=request_count)
        return MemoryRerankDecision(
            selected=selected,
            rejected=rejected,
            usage=usage
        )
