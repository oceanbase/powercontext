"""Shared helpers for local embedding model cache management.

CN-aware download + HuggingFace cache bridge for the default sentence-transformers
model (``all-MiniLM-L6-v2``). Used by both:

* :class:`powermem.integrations.embeddings.pyseekdb_default.PyseekdbDefaultEmbedding`
* :class:`powermem.integrations.embeddings.huggingface.HuggingFaceEmbedding`

Extracted so the SQLite/HuggingFace path gets the same CN detection + ModelScope
fallback that the OceanBase/pyseekdb path already had, avoiding the
``huggingface_hub`` httpx-client-closed retry bug on networks where
``huggingface.co`` is unreachable.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)


DEFAULT_MODEL_NAME = "all-MiniLM-L6-v2"
DEFAULT_MODEL_REPO_ID = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_EMBEDDING_DIMS = 384

_MODEL_DOWNLOAD_TIMEOUT_S = 30

_MODELSCOPE_REPO_ID = "AI-ModelScope/all-MiniLM-L6-v2"
_HF_REVISION_FALLBACK = "fa97f6e7cb1a59073dff9e9d8ba1c7c1591cc08d"

_IP_COUNTRY_URLS = [
    "https://ipapi.co/country/",
    "https://ifconfig.co/country-iso",
    "https://ipinfo.io/country",
]


def is_model_cached(repo_id: str) -> bool:
    """Return True if the HuggingFace model is already in the local cache."""
    try:
        from huggingface_hub import try_to_load_from_cache

        result = try_to_load_from_cache(repo_id, "config.json")
        return result is not None
    except Exception:
        return False


def detect_country(timeout: int = 3) -> str:
    """Detect public IP country code.

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


def download_via_modelscope(country: str = "") -> None:
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


def bridge_modelscope_to_hf_cache() -> None:
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


def load_sentence_transformer_with_fallback(model_name: str, repo_id: str, on_model_loaded=None):
    """Ensure the embedding model is cached, then load it with SentenceTransformer.

    Flow:
    1. Cache hit → load with ``local_files_only=True`` (no network).
    2. Cache miss + CN → download via ModelScope + bridge to HF cache, then load.
    3. Cache miss + non-CN / detection failed → HF download with timeout.

    ``on_model_loaded(model_name, model)`` is an optional callback invoked after
    the model is loaded in any path (used by pyseekdb_default to patch pyseekdb's
    internal cache). ``sentence_transformers`` is optional; if not installed, the
    download still runs so model files are available, but ``None`` is returned.
    """
    if is_model_cached(repo_id):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            logger.debug(
                "sentence_transformers not installed; skipping model pre-warm "
                "(install the 'extras' group to enable the cache-first optimization)"
            )
            return None
        model = SentenceTransformer(model_name, local_files_only=True)
        if on_model_loaded is not None:
            on_model_loaded(model_name, model)
        logger.info("Loaded %s from local cache", model_name)
        return model

    logger.debug("Model %s not in cache; detecting network region...", model_name)
    country = detect_country()
    logger.info(
        "Network region: %s; downloading %s via %s",
        country or "unknown",
        model_name,
        "ModelScope" if country == "CN" else "HuggingFace",
    )

    if country == "CN":
        try:
            download_via_modelscope(country=country)
            bridge_modelscope_to_hf_cache()
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
        if on_model_loaded is not None:
            on_model_loaded(model_name, model)
        logger.info("Downloaded and loaded %s via ModelScope", model_name)
        return model

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        logger.debug(
            "sentence_transformers not installed; skipping model pre-warm "
            "(install the 'extras' group to enable the cache-first optimization)"
        )
        return None

    import threading

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
        if on_model_loaded is not None:
            on_model_loaded(model_name, result[0])
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
