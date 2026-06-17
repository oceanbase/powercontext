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

import json
import logging
import os
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path
from typing import List, Literal, Optional

from powermem.integrations.embeddings.base import EmbeddingBase
from powermem.integrations.embeddings.config.base import BaseEmbedderConfig

logger = logging.getLogger(__name__)

logging.getLogger("onnxruntime").setLevel(logging.WARNING)
logging.getLogger("huggingface_hub").setLevel(logging.WARNING)


# Match pyseekdb's DefaultEmbeddingFunction so the two systems agree on the
# default model and dimension.
DEFAULT_MODEL_NAME = "all-MiniLM-L6-v2"
DEFAULT_MODEL_REPO_ID = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_EMBEDDING_DIMS = 384

# Timeout (seconds) for the HuggingFace download path (non-CN).
_MODEL_DOWNLOAD_TIMEOUT_S = 30

_MODELSCOPE_REPO_ID = "AI-ModelScope/all-MiniLM-L6-v2"
# Fallback HF revision SHA used when the HF API is unreachable during bridge.
_HF_REVISION_FALLBACK = "fa97f6e7cb1a59073dff9e9d8ba1c7c1591cc08d"

_IP_COUNTRY_URLS = [
    "https://ipapi.co/country/",
    "https://ifconfig.co/country-iso",
    "https://ipinfo.io/country",
]


def _is_model_cached(repo_id: str) -> bool:
    """Return True if the HuggingFace model is already in the local cache."""
    try:
        from huggingface_hub import try_to_load_from_cache

        result = try_to_load_from_cache(repo_id, "config.json")
        return result is not None
    except Exception:
        return False


def _detect_country(timeout: int = 3) -> str:
    """Detect public IP country code, mirroring common.sh detect_public_ip_country.

    Tries up to three IP geolocation APIs in sequence.  Returns the two-letter
    ISO country code (upper-case) on success, or an empty string if all APIs
    fail or time out.
    """
    for url in _IP_COUNTRY_URLS:
        try:
            resp = urllib.request.urlopen(url, timeout=timeout)
            code = resp.read().decode().strip().upper()[:2]
            if len(code) == 2 and code.isalpha():
                return code
        except Exception:
            continue
    return ""


def _download_via_modelscope(country: str = "") -> None:
    """Download the default embedding model via ModelScope using ``uvx``.

    Reads the following environment variables (set by ``common.sh`` / init.sh):
    - ``POWERMEM_UV_BIN`` — path to the ``uv`` binary
    - ``POWERMEM_BOOTSTRAP_PYTHON`` — Python version/path for ``uvx --python``
    - ``POWERMEM_UV_INDEX_URL`` — custom PyPI index (e.g. Tsinghua mirror)
    - ``POWERMEM_MODELSCOPE_PACKAGE`` — override the modelscope package spec
    """
    uv_bin = (
        os.environ.get("POWERMEM_UV_BIN")
        or shutil.which("uv")
        or str(Path.home() / ".local" / "bin" / "uv")
    )
    if not shutil.which(uv_bin) and not os.path.isfile(uv_bin):
        raise RuntimeError(
            "uv is required to download the embedding model via ModelScope but "
            "was not found on PATH. "
            "Install it: https://docs.astral.sh/uv/getting-started/installation/"
        )

    python = os.environ.get("POWERMEM_BOOTSTRAP_PYTHON", "3.11")
    package = os.environ.get("POWERMEM_MODELSCOPE_PACKAGE", "modelscope")

    index_url = os.environ.get("POWERMEM_UV_INDEX_URL", "")
    if not index_url and country == "CN":
        # Mirror common.sh configure_uv_index: default to Tsinghua for CN
        index_url = "https://pypi.tuna.tsinghua.edu.cn/simple"

    cmd = [uv_bin, "tool", "run", "--python", python]
    if index_url:
        cmd += ["--default-index", index_url]
    cmd += [
        "--from", package,
        "python", "-c",
        f"from modelscope import snapshot_download; "
        f"snapshot_download({_MODELSCOPE_REPO_ID!r})",
    ]

    logger.info("Downloading embedding model via ModelScope: %s", _MODELSCOPE_REPO_ID)
    subprocess.run(cmd, check=True)


def _bridge_modelscope_to_hf_cache() -> None:
    """Copy a ModelScope-downloaded model into the HuggingFace hub cache layout.

    Mirrors the bridge logic in ``preload-model.sh``.
    """
    org, name = _MODELSCOPE_REPO_ID.split("/", 1)
    src = Path.home() / ".cache" / "modelscope" / "hub" / "models" / org / name
    if not src.exists():
        raise RuntimeError(
            f"ModelScope cache not found at {src}; the download may have failed."
        )

    hf_dir_name = "models--" + DEFAULT_MODEL_REPO_ID.replace("/", "--")
    hub = Path.home() / ".cache" / "huggingface" / "hub" / hf_dir_name

    try:
        resp = urllib.request.urlopen(
            f"https://huggingface.co/api/models/{DEFAULT_MODEL_REPO_ID}",
            timeout=5,
        )
        rev = json.load(resp)["sha"]
    except Exception:
        rev = _HF_REVISION_FALLBACK

    snap = hub / "snapshots" / rev
    snap.mkdir(parents=True, exist_ok=True)
    refs_dir = hub / "refs"
    refs_dir.mkdir(exist_ok=True)
    (refs_dir / "main").write_text(rev)

    skip = {"configuration.json", "data_config.json"}
    for fname in os.listdir(src):
        if fname in skip:
            continue
        source = src / fname
        target = snap / fname
        if target.exists():
            continue
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)

    logger.info("Bridged ModelScope cache to HuggingFace cache: %s", snap)


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

    Flow:
    1. Cache hit → load with ``local_files_only=True`` and patch pyseekdb cache.
    2. Cache miss + CN → download via ModelScope + bridge to HF cache, then load.
    3. Cache miss + non-CN / detection failed → HF download via background thread
       with :data:`_MODEL_DOWNLOAD_TIMEOUT_S` timeout.

    ``sentence_transformers`` is optional.  If not installed, the download (CN
    or otherwise) still runs so the model files are available for pyseekdb's own
    ONNX-based loader; only the pre-warm / cache-patch step is skipped.
    """
    if _is_model_cached(repo_id):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            logger.debug(
                "sentence_transformers not installed; skipping model pre-warm "
                "(install the 'extras' group to enable the cache-first optimization)"
            )
            return None
        model = SentenceTransformer(model_name, local_files_only=True)
        _patch_sentence_transformer_cache(model_name, model)
        logger.info("Loaded %s from local cache", model_name)
        return model

    # Cache miss: detect network region and download
    logger.debug("Model %s not in cache; detecting network region...", model_name)
    country = _detect_country()
    logger.info(
        "Network region: %s; downloading %s via %s",
        country or "unknown",
        model_name,
        "ModelScope" if country == "CN" else "HuggingFace",
    )

    if country == "CN":
        try:
            _download_via_modelscope(country=country)
            _bridge_modelscope_to_hf_cache()
        except Exception as exc:
            raise RuntimeError(
                f"Failed to download embedding model '{model_name}' via ModelScope: "
                f"{exc}.\n"
                "Download it manually:\n"
                f"  python -c \"from modelscope import snapshot_download; "
                f"snapshot_download({_MODELSCOPE_REPO_ID!r})\""
            ) from exc

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            logger.debug(
                "sentence_transformers not installed; skipping model pre-warm "
                "(install the 'extras' group to enable the cache-first optimization)"
            )
            return None
        model = SentenceTransformer(model_name, local_files_only=True)
        _patch_sentence_transformer_cache(model_name, model)
        logger.info("Downloaded and loaded %s via ModelScope", model_name)
        return model

    # Non-CN / detection failed: HF download with timeout
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        logger.debug(
            "sentence_transformers not installed; skipping model pre-warm "
            "(install the 'extras' group to enable the cache-first optimization)"
        )
        return None

    logger.debug(
        "Attempting HuggingFace download of %s (timeout %ss)...",
        model_name,
        _MODEL_DOWNLOAD_TIMEOUT_S,
    )

    result: list = [None]
    error: list = [None]

    def _load() -> None:
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
            f"Failed to download embedding model '{model_name}': {error[0]}.\n"
            "Download it manually:\n"
            f"  python -c \"from modelscope import snapshot_download; "
            f"snapshot_download({_MODELSCOPE_REPO_ID!r})\""
        ) from error[0]

    raise RuntimeError(
        f"Downloading embedding model '{model_name}' timed out after "
        f"{_MODEL_DOWNLOAD_TIMEOUT_S}s. The model is not cached and the "
        f"network is unreachable.\n"
        "Download it manually:\n"
        f"  python -c \"from modelscope import snapshot_download; "
        f"snapshot_download({_MODELSCOPE_REPO_ID!r})\""
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
