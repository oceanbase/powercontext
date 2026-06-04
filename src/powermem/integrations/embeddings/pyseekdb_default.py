"""Built-in default embedder.

Wraps ``pyseekdb.client.embedding_function.DefaultEmbeddingFunction`` so PowerMem
can start with zero configuration and no external API key. The model is
``sentence-transformers/all-MiniLM-L6-v2`` (384-dim), the same default used by
pyseekdb. It downloads to a local cache on first use and runs locally afterwards.

When the network is slow or blocked (e.g. behind a firewall or GFW),
``sentence_transformers`` contacts ``huggingface.co`` to check for updates even
when the model is already cached, causing a 30-60s hang on every server start.
We detect the cache state first — if cached, we force ``HF_HUB_OFFLINE=1`` so
all downstream calls (including pyseekdb's internal ``SentenceTransformer``)
use the cache immediately.  If not cached, we attempt a download with a timeout
and provide a friendly error with manual instructions on failure.

Override via the ``embedder`` section of :class:`~powermem.configs.MemoryConfig`
to switch to a production-grade provider (OpenAI, Qwen, SiliconFlow, etc.).
"""

from __future__ import annotations

import logging
import threading
from typing import List, Literal, Optional

from powermem.integrations.embeddings.base import EmbeddingBase
from powermem.integrations.embeddings.config.base import BaseEmbedderConfig

logger = logging.getLogger(__name__)

logging.getLogger("onnxruntime").setLevel(logging.WARNING)
logging.getLogger("huggingface_hub").setLevel(logging.WARNING)


# Match pyseekdb's DefaultEmbeddingFunction so the two systems agree on the
# default model and dimension. Keeping this constant local avoids importing
# pyseekdb at module import time.
DEFAULT_MODEL_NAME = "all-MiniLM-L6-v2"
DEFAULT_MODEL_REPO_ID = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_EMBEDDING_DIMS = 384

# Timeout (seconds) for downloading the embedding model.  When the network
# is blocked the huggingface_hub retry loop can take 2-3 minutes; this
# caps it so the server fails fast with a helpful error.
_MODEL_DOWNLOAD_TIMEOUT_S = 30


def _is_model_cached(model_name: str) -> bool:
    """Check if a HuggingFace model is already in the local cache."""
    try:
        from huggingface_hub import try_to_load_from_cache

        result = try_to_load_from_cache(model_name, "config.json")
        return result is not None  # None = not cached
    except Exception:
        return False


def _load_sentence_transformer_with_fallback(model_name: str, repo_id: str):
    """Load a SentenceTransformer with cache-first logic and download timeout.

    1. **Cache hit** → load from cache and monkey-patch downstream so pyseekdb's
       internal ``SentenceTransformer`` call re-uses the already-loaded instance
       instead of triggering its own (network-stuck) load.
    2. **Cache miss** → attempt a real download in a background thread with a
       timeout.  If the download succeeds, use it.  If it times out or fails,
       raise a clear error with manual download instructions.

    ``sentence_transformers`` is an optional dependency (the ``extras`` group).
    When it is not installed we skip this pre-warm step entirely and let
    pyseekdb's ``DefaultEmbeddingFunction`` load the model itself via
    ``onnxruntime`` — the embedder still works, it just misses the cache-first
    optimization that avoids a possible huggingface.co hang on slow networks.

    Args:
        model_name: short name passed to SentenceTransformer (e.g. "all-MiniLM-L6-v2")
        repo_id: full HuggingFace repo ID (e.g. "sentence-transformers/all-MiniLM-L6-v2")
    """
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        logger.debug(
            "sentence_transformers not installed; skipping model pre-warm "
            "(install the 'extras' group to enable the cache-first optimization)"
        )
        return None

    if _is_model_cached(repo_id):
        model = SentenceTransformer(model_name, local_files_only=True)
        _patch_sentence_transformer_cache(model_name, model)
        logger.info("Loaded %s from local cache", model_name)
        return model

    # Cache miss: attempt download with timeout.
    logger.debug(
        "Model %s not in cache, attempting download (timeout %ss)…",
        model_name,
        _MODEL_DOWNLOAD_TIMEOUT_S,
    )

    result: list = [None]
    error: list = [None]

    def _load():
        try:
            result[0] = SentenceTransformer(model_name)
        except Exception as exc:
            error[0] = exc

    thread = threading.Thread(target=_load, daemon=True)
    thread.start()
    thread.join(timeout=_MODEL_DOWNLOAD_TIMEOUT_S)

    if result[0] is not None:
        _patch_sentence_transformer_cache(model_name, result[0])
        logger.info("Downloaded and loaded %s", model_name)
        return result[0]

    if error[0] is not None:
        raise RuntimeError(
            f"Failed to download embedding model '{model_name}': "
            f"{error[0]}. "
            f"Download it manually: "
            f'python -c "from modelscope import snapshot_download; '
            f"snapshot_download('AI-ModelScope/all-MiniLM-L6-v2')\""
        ) from error[0]

    raise RuntimeError(
        f"Downloading embedding model '{model_name}' timed out after "
        f"{_MODEL_DOWNLOAD_TIMEOUT_S}s. The model is not cached and the "
        f"network is unreachable. "
        f"Download it manually: "
        f'python -c "from modelscope import snapshot_download; '
        f"snapshot_download('AI-ModelScope/all-MiniLM-L6-v2')\""
    )


def _patch_sentence_transformer_cache(model_name: str, model):
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

        # Pre-load the model with cache-first fallback.  This ensures the
        # model is available BEFORE pyseekdb's DefaultEmbeddingFunction()
        # creates its own SentenceTransformer (which would otherwise hang
        # on a blocked network).  HF_HUB_OFFLINE=1 is set at module level
        # so pyseekdb's internal call also hits the cache.
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
