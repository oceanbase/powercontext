"""Local multilingual text embeddings.

The point is to keep the reviewer's setup small and private: no OpenAI key, no
download of a large model *service*. Two providers are supported:

``sentence-transformers``
    A small multilingual sentence-transformer run on the local CPU
    (``paraphrase-multilingual-MiniLM-L12-v2``, 384 dimensions, ~470 MB on first use).

``hash``
    A deterministic character n-gram hashing embedder with no dependency at all.
    It is not semantic like the transformer, but it makes the whole pipeline
    (hybrid search, memory, tests) runnable offline and keeps the demo honest:
    the retrieval quality numbers in ``docs/技术使用记录.md`` state which
    provider produced them.

``auto`` (default) tries sentence-transformers and falls back to ``hash``.
"""

from __future__ import annotations

import hashlib
import math
import re
from functools import lru_cache
from typing import Iterable, List

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


def _tokens(text: str) -> List[str]:
    return [token.lower() for token in _TOKEN_RE.findall(text or "")]


def _l2_normalize(vector: List[float]) -> List[float]:
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


class HashEmbedder:
    """Character n-gram hashing; deterministic across processes and machines."""

    name = "hash-ngram"

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    def _one(self, text: str) -> List[float]:
        vector = [0.0] * self.dim
        tokens = _tokens(text)
        grams = list(tokens)
        grams.extend("".join(tokens[i : i + 2]) for i in range(len(tokens) - 1))
        grams.extend(text[i : i + 3] for i in range(max(0, len(text) - 2)))
        for gram in grams:
            if not gram.strip():
                continue
            digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        return _l2_normalize(vector)

    def encode(self, texts: Iterable[str]) -> List[List[float]]:
        return [self._one(text) for text in texts]


class SentenceTransformerEmbedder:
    """Thin wrapper so the rest of the code sees one interface."""

    name = "sentence-transformers"

    def __init__(self, model_name: str) -> None:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415 - optional

        self._model = SentenceTransformer(model_name)
        self.dim = int(self._model.get_sentence_embedding_dimension())
        self.name = f"sentence-transformers:{model_name}"

    def encode(self, texts: Iterable[str]) -> List[List[float]]:
        vectors = self._model.encode(list(texts), normalize_embeddings=True, show_progress_bar=False)
        return [[float(value) for value in vector] for vector in vectors]


@lru_cache(maxsize=1)
def get_embedder(provider: str = "auto", model: str = "paraphrase-multilingual-MiniLM-L12-v2", dim: int = 384):
    """Return the embedder once; ``auto`` degrades to the hashing embedder."""
    if provider in {"auto", "sentence-transformers", "st"}:
        try:
            return SentenceTransformerEmbedder(model)
        except Exception:  # noqa: BLE001 - optional dependency, fall back on purpose
            if provider != "auto":
                raise
    return HashEmbedder(dim=dim)


def embed(texts: Iterable[str], provider: str, model: str, dim: int) -> List[List[float]]:
    return get_embedder(provider, model, dim).encode(texts)


def vector_literal(vector: List[float]) -> str:
    """seekdb accepts a JSON array literal as a VECTOR value."""
    return "[" + ",".join(f"{value:.6f}" for value in vector) + "]"
