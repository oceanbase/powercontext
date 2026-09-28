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

"""MiniMax embedding backend adapter.

MiniMax publishes embeddings under the OpenAI path prefix but does not implement
the OpenAI embeddings contract. Its ``/v1/embeddings`` endpoint requires the
native request shape ``{"model", "texts": [...], "type"}`` instead of OpenAI's
``{"input": [...]}``, and returns ``{"vectors": [...], "base_resp": {"status_code"}}``
instead of ``{"data": [{"embedding": [...]}]}``. It also answers HTTP 200 with a
non-zero ``base_resp.status_code`` on error rather than a 4xx/5xx. This adapter
speaks the MiniMax-native shape directly so PowerContext can use MiniMax as an
embedding provider without a custom Pydantic AI provider shim.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from urllib.parse import urlparse

import httpx

from powercontext.builtin.artifacts.memory.canonical import canonical_embedding
from powercontext.builtin.artifacts.memory.models import EmbeddingProfile
from powercontext.builtin.inference.errors import (
    InferenceConfigurationError,
    InferenceTimeoutError,
    InferenceUnavailableError,
    InvalidInferenceOutputError,
)
from powercontext.builtin.inference.models import EmbeddingResult, InferenceUsage

_MINIMAX_DOCUMENT_TYPE = "db"
_MINIMAX_QUERY_TYPE = "query"


class MiniMaxEmbeddingModel:
    """Embed an ordered text batch with MiniMax ``embo`` via ``/v1/embeddings``."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        headers: Mapping[str, str] | None = None,
        profile: EmbeddingProfile,
        batch_size: int = 10,
        timeout_seconds: float = 30.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not model or not model.strip():
            raise InferenceConfigurationError("embedding-model-empty")
        if profile.dimension < 1:
            raise InferenceConfigurationError("embedding-dimension-positive")
        if batch_size < 1:
            raise InferenceConfigurationError("embedding-batch-size-positive")
        if not profile.profile_id.strip() or not profile.model.strip() or not profile.normalization.strip():
            raise InferenceConfigurationError("embedding-profile-identifiers")
        self.profile = profile
        self._model = model
        self._batch_size = batch_size
        self._timeout = timeout_seconds
        self._endpoint = f"{base_url.rstrip('/')}/embeddings"
        request_headers: dict[str, str] = {"Content-Type": "application/json"}
        if headers:
            request_headers.update(headers)
        self._headers = request_headers
        self._client = http_client or httpx.AsyncClient(timeout=timeout_seconds)

    async def aclose(self) -> None:
        """Close the underlying HTTP client (registered with the runtime stack)."""

        await self._client.aclose()

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Embed documents, validating order, count, dimension, and finite values."""

        return await self._embed(texts, embedding_type=_MINIMAX_DOCUMENT_TYPE)

    async def embed_query(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Embed retrieval queries, validating order, count, dimension, and finite values."""

        return await self._embed(texts, embedding_type=_MINIMAX_QUERY_TYPE)

    async def _embed(self, texts: tuple[str, ...], *, embedding_type: str) -> EmbeddingResult:
        """Embed one MiniMax document or query batch."""

        if not texts:
            return EmbeddingResult(vectors=())

        try:
            result = await asyncio.wait_for(
                self._embed_batches(texts, embedding_type=embedding_type), timeout=self._timeout
            )
        except asyncio.CancelledError:
            raise
        except (InvalidInferenceOutputError, InferenceConfigurationError):
            raise
        except TimeoutError as error:
            raise InferenceTimeoutError("embed", self._timeout) from error
        except httpx.TimeoutException as error:
            raise InferenceTimeoutError("embed", self._timeout) from error
        except httpx.HTTPError as error:
            raise InferenceUnavailableError("embed") from error
        return result

    async def _embed_batches(self, texts: tuple[str, ...], *, embedding_type: str) -> EmbeddingResult:
        vectors: list[tuple[float, ...]] = []
        requests = 0
        input_tokens = 0
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            rows, tokens = await self._embed_one(batch, embedding_type=embedding_type)
            vectors.extend(self._validated_vectors(batch, rows))
            requests += 1
            input_tokens += tokens
        return EmbeddingResult(
            vectors=tuple(vectors),
            usage=InferenceUsage(requests=requests, input_tokens=input_tokens, output_tokens=None),
        )

    async def _embed_one(self, batch: Sequence[str], *, embedding_type: str) -> tuple[list[list[float]], int]:
        payload = {"model": self._model, "texts": list(batch), "type": embedding_type}
        response = await self._client.post(self._endpoint, json=payload, headers=self._headers)
        # MiniMax returns HTTP 200 with a non-zero base_resp.status_code on error;
        # only a real transport/HTTP failure reaches raise_for_status first.
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as error:
            raise InvalidInferenceOutputError("embed", "provider response was not valid JSON") from error
        if not isinstance(data, Mapping):
            raise InvalidInferenceOutputError("embed", "provider response was not a JSON object")
        base_resp = data.get("base_resp")
        if isinstance(base_resp, Mapping):
            status_code = base_resp.get("status_code")
            if status_code is not None and status_code != 0:
                status_msg = base_resp.get("status_msg")
                detail = f"provider returned MiniMax status_code {status_code}"
                if isinstance(status_msg, str) and status_msg.strip():
                    detail = f"{detail}: {status_msg}"
                raise InferenceUnavailableError("embed", detail)
        vectors = data.get("vectors")
        if not isinstance(vectors, list) or len(vectors) != len(batch):
            raise InvalidInferenceOutputError("embed", "provider returned no vectors or the wrong vector count")
        total_tokens = data.get("total_tokens")
        tokens = int(total_tokens) if isinstance(total_tokens, int) else 0
        return vectors, tokens

    def _validated_vectors(
        self, texts: Sequence[str], rows: Sequence[Sequence[float]]
    ) -> tuple[tuple[float, ...], ...]:
        out: list[tuple[float, ...]] = []
        for row in rows:
            try:
                out.append(
                    canonical_embedding(
                        tuple(row),
                        dimension=self.profile.dimension,
                        normalization=self.profile.normalization,
                    )
                )
            except (TypeError, ValueError) as error:
                raise InvalidInferenceOutputError("embed", str(error)) from error
        return tuple(out)


def _embedding_model_name(model: str | None) -> str:
    """Strip a provider prefix such as ``openai:embo-01`` or ``minimax:embo-01``."""

    if model is None:
        return ""
    return model.partition(":")[2] or model


def is_minimax_embedding(base_url: str | None, model: str | None) -> bool:
    """Detect a MiniMax embedding endpoint by host or explicit model prefix."""

    host = (urlparse(base_url or "").hostname or "").lower()
    if any(host == domain or host.endswith(f".{domain}") for domain in ("minimaxi.com", "minimax.io")):
        return True
    provider_prefix, separator, _model_name = (model or "").partition(":")
    return bool(separator) and provider_prefix.lower() == "minimax"


__all__ = ["MiniMaxEmbeddingModel", "is_minimax_embedding"]
