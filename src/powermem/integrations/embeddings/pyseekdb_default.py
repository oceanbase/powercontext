"""Built-in default embedder.

Wraps ``pyseekdb.client.embedding_function.DefaultEmbeddingFunction`` so PowerMem
can start with zero configuration and no external API key. The model is
``sentence-transformers/all-MiniLM-L6-v2`` (384-dim), the same default used by
pyseekdb. It downloads to a local cache on first use and runs locally afterwards.

On first use when the model is not cached, the download source is chosen based on
the public IP's country code (same logic as ``common.sh``):

* **CN** — downloads via ModelScope (``AI-ModelScope/all-MiniLM-L6-v2``) using
  ``uvx``, then bridges the ModelScope cache into the HuggingFace hub cache layout
  so all downstream tools see a normal HF cache.  The PyPI index defaults to the
  Tsinghua mirror unless ``POWERMEM_UV_INDEX_URL`` is already set.
* **Non-CN / detection failed** — downloads directly from HuggingFace with a
  :data:`_MODEL_DOWNLOAD_TIMEOUT_S`-second timeout.

When the model is already cached, the country detection and download are skipped
entirely; ``HF_HUB_OFFLINE`` behaviour is achieved by calling
``SentenceTransformer(local_files_only=True)`` and injecting the result into
pyseekdb's internal model cache so pyseekdb's own ``SentenceTransformer`` call
does not attempt a network round-trip.

Override via the ``embedder`` section of :class:`~powermem.configs.MemoryConfig`
to switch to a production-grade provider (OpenAI, Qwen, SiliconFlow, etc.).
"""

from __future__ import annotations

import logging
from typing import List, Literal, Optional

from powermem.integrations.embeddings._model_cache import (
    DEFAULT_EMBEDDING_DIMS,
    DEFAULT_MODEL_NAME,
    DEFAULT_MODEL_REPO_ID,
    _HF_REVISION_FALLBACK,
    _MODELSCOPE_REPO_ID,
    bridge_modelscope_to_hf_cache as _bridge_modelscope_to_hf_cache,
    detect_country as _detect_country,
    download_via_modelscope as _download_via_modelscope,
    is_model_cached as _is_model_cached,
    load_sentence_transformer_with_fallback,
)
from powermem.integrations.embeddings.base import EmbeddingBase
from powermem.integrations.embeddings.config.base import BaseEmbedderConfig

logger = logging.getLogger(__name__)

logging.getLogger("onnxruntime").setLevel(logging.WARNING)
logging.getLogger("huggingface_hub").setLevel(logging.WARNING)


def _patch_sentence_transformer_cache(model_name: str, model) -> None:
    """Inject a pre-loaded model into pyseekdb's internal cache.

    pyseekdb's ``SentenceTransformerEmbeddingFunction`` keeps a class-level
    ``models`` dict.  If the model is already present, it skips the expensive
    ``SentenceTransformer(model_name)`` call — which would otherwise try to
    reach ``huggingface.co`` even with a cached model.
    """
    try:
        from pyseekdb.utils.embedding_functions.sentence_transformer_embedding_function import (
            SentenceTransformerEmbeddingFunction,
        )

        SentenceTransformerEmbeddingFunction.models[model_name] = model
        logger.debug("Patched pyseekdb SentenceTransformer cache for %s", model_name)
    except ImportError:
        logger.debug("Could not patch pyseekdb cache (module not available)")


def _load_sentence_transformer_with_fallback(model_name: str, repo_id: str):
    """Ensure the embedding model is cached, then load it with SentenceTransformer.

    Thin wrapper around :func:`load_sentence_transformer_with_fallback` that also
    patches pyseekdb's internal model cache after load. See the shared helper in
    ``_model_cache.py`` for the full CN-aware download flow.
    """
    return load_sentence_transformer_with_fallback(
        model_name,
        repo_id,
        on_model_loaded=_patch_sentence_transformer_cache,
    )


class PyseekdbDefaultEmbedding(EmbeddingBase):
    """Zero-config local embedder backed by pyseekdb's DefaultEmbeddingFunction."""

    def __init__(self, config: Optional[BaseEmbedderConfig] = None):
        super().__init__(config)

        try:
            from pyseekdb.client.embedding_function import DefaultEmbeddingFunction
        except ImportError as exc:
            raise ImportError(
                "pyseekdb is required for the built-in default embedder. "
                'Install it with: pip install "powermem[seekdb]"'
            ) from exc

        _load_sentence_transformer_with_fallback(
            DEFAULT_MODEL_NAME,
            DEFAULT_MODEL_REPO_ID,
        )

        self._fn = DefaultEmbeddingFunction()
        self.config.model = self.config.model or DEFAULT_MODEL_NAME
        self.config.embedding_dims = (
            self.config.embedding_dims or DEFAULT_EMBEDDING_DIMS
        )

        logger.info(
            "PyseekdbDefaultEmbedding ready (model=%s, dims=%s)",
            self.config.model,
            self.config.embedding_dims,
        )

    def embed(
        self,
        text,
        memory_action: Optional[Literal["add", "search", "update"]] = None,
    ):
        """Return a single embedding vector for ``text``."""
        del memory_action  # unused: default embedder treats all actions identically
        if text is None:
            raise ValueError("text must not be None")
        embeddings = self._fn([text] if isinstance(text, str) else list(text))
        if not embeddings:
            raise RuntimeError("default embedder returned no vectors")
        return list(embeddings[0])

    def embed_batch(
        self,
        texts: List[str],
        memory_action: Optional[Literal["add", "search", "update"]] = None,
    ) -> List[List[float]]:
        """Batch embedding using the underlying ONNX model directly."""
        del memory_action  # unused: default embedder treats all actions identically
        if not texts:
            return []
        return [list(vec) for vec in self._fn(list(texts))]
